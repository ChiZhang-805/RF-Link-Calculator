"""Authenticated desktop/cloud gateway. The legacy server remains loopback-only."""

import logging
import mimetypes
import multiprocessing
import os
import secrets
import smtplib
import ssl
import threading
from dataclasses import dataclass
from email.message import EmailMessage
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from rf_link_calculator.accounts import Accounts, email_address
from rf_link_calculator.domain.codec import project_from_dict
from rf_link_calculator.presentation.server import ASSETS, bootstrap, dispatch

LOGGER = logging.getLogger(__name__)
COOKIE = "rf_session"


@dataclass
class Settings:
    directory: Path
    hosted: bool = False
    public_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = ""

    @classmethod
    def environment(cls):
        return cls(
            Path(os.getenv("RF_LINK_DATA_DIR", str(Path.home() / "RF-Link-Calculator-Data"))),
            os.getenv("RF_MODE", "desktop") == "hosted",
            os.getenv("RF_PUBLIC_URL", "").rstrip("/"),
            os.getenv("RF_SMTP_HOST", ""),
            int(os.getenv("RF_SMTP_PORT", "587")),
            os.getenv("RF_SMTP_USER", ""),
            os.getenv("RF_SMTP_PASSWORD", ""),
            os.getenv("RF_MAIL_FROM", ""),
        )

    def validate(self):
        if self.hosted:
            url = urlsplit(self.public_url)
            if url.scheme != "https" or not url.hostname or url.path or url.query or url.fragment:
                raise RuntimeError("RF_PUBLIC_URL 必须是部署后的 HTTPS 站点地址")
            if not self.smtp_host or not self.mail_from:
                raise RuntimeError("云端注册与找回密码需要配置 RF_SMTP_HOST 和 RF_MAIL_FROM")

    def send(self, email, purpose, token):
        message = EmailMessage()
        message["Subject"] = "RF Link · " + ("验证邮箱" if purpose == "verify" else "重置密码")
        message["From"], message["To"] = self.mail_from, email
        message.set_content(
            f"{self.public_url}/#{purpose}={token}\n\n链接有效期为 30 分钟。若非本人操作，请忽略。"
        )
        smtp_type = smtplib.SMTP_SSL if self.smtp_port == 465 else smtplib.SMTP
        with smtp_type(self.smtp_host, self.smtp_port, timeout=15) as smtp:
            if self.smtp_port != 465:
                smtp.starttls(context=ssl.create_default_context())
            if self.smtp_user:
                smtp.login(self.smtp_user, self.smtp_password)
            smtp.send_message(message)


def _worker(pipe, action, data):
    try:
        pipe.send((True, dispatch(action, data)))
    except (ValueError, TypeError, KeyError) as exc:
        pipe.send((False, str(exc)))
    except Exception:
        pipe.send((False, "计算未完成"))
    finally:
        pipe.close()


def isolated_dispatch(action, data, timeout=120):
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_worker, args=(sender, action, data), daemon=True)
    process.start()
    sender.close()
    try:
        if not receiver.poll(timeout):
            raise ValueError("计算超时，请减少分析规模")
        ok, value = receiver.recv()
        if not ok:
            raise ValueError(value)
        return value
    finally:
        receiver.close()
        process.join(1)
        if process.is_alive():
            process.terminate()
            process.join(5)
        if process.is_alive():
            process.kill()
            process.join()


def create_app(settings=None):
    settings = settings or Settings.environment()
    settings.validate()
    store = Accounts(settings.directory / "accounts.sqlite3")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.accounts = store
    capacity = threading.BoundedSemaphore(2)
    root = files("rf_link_calculator").joinpath("presentation", "web")

    def session(request):
        user = store.session(request.cookies.get(COOKIE, ""))
        if not user:
            raise HTTPException(401, "请先登录")
        return user

    def limit(request, action, email="", maximum=10):
        peer = request.client.host if request.client else "local"
        if not store.limit(f"{action}:ip:{peer}", maximum * 3) or (
            email and not store.limit(f"{action}:email:{email}", maximum)
        ):
            raise HTTPException(429, "操作过于频繁，请稍后重试")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        try:
            hostname = urlsplit("http://" + host).hostname
        except ValueError:
            hostname = None
        allowed = (
            hostname == urlsplit(settings.public_url).hostname
            if settings.hosted
            else hostname in ("127.0.0.1", "localhost")
        )
        if not allowed:
            return JSONResponse({"error": "请求主机无效"}, 403)
        if request.method not in ("GET", "HEAD"):
            origin = settings.public_url if settings.hosted else "http://" + host
            if request.headers.get("origin") != origin:
                return JSONResponse({"error": "请求来源无效"}, 403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"error": "请求格式无效"}, 415)
            maximum = 16384 if request.url.path.startswith("/auth/") else 34 * 1024 * 1024
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > maximum:
                    return JSONResponse({"error": "请求大小超限"}, 413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
            if not request.url.path.startswith("/auth/"):
                user = store.session(request.cookies.get(COOKIE, ""))
                if not user:
                    return JSONResponse({"error": "请先登录"}, 401)
                if not secrets.compare_digest(user["csrf"], request.headers.get("x-csrf-token", "")):
                    return JSONResponse({"error": "会话校验失败，请重新登录"}, 403)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
            }
        )
        if settings.hosted:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({"error": exc.detail}, exc.status_code)

    async def body(request):
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeError):
            raise HTTPException(400, "请求必须为 JSON 对象") from None

    @app.get("/health")
    def health():
        return {"status": "ok", "accounts": True}

    @app.get("/auth/config")
    def auth_config():
        return {"hosted": settings.hosted}

    @app.post("/auth/{action}")
    async def auth(action: str, request: Request):
        data = await body(request)
        try:
            return await run_in_threadpool(auth_sync, action, request, data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except (smtplib.SMTPException, OSError):
            raise HTTPException(503, "邮件服务暂不可用，请稍后重试") from None

    def auth_sync(action, request, data):
        if action == "logout":
            store.logout(request.cookies.get(COOKIE, ""))
            response = JSONResponse({"ok": True})
            response.delete_cookie(COOKIE)
            return response
        if action not in ("register", "login", "forgot", "resend", "verify", "reset", "recover", "password"):
            raise HTTPException(404, "未找到")
        email = (
            email_address(data.get("email", ""))
            if action in ("register", "login", "forgot", "resend", "recover")
            else ""
        )
        limit(request, action, email, 6 if action in ("forgot", "resend", "register") else 10)
        if action == "register":
            uid, recovery = store.register(email, data.get("password"), verified=not settings.hosted)
            if settings.hosted:
                settings.send(email, "verify", store.issue(uid, "verify"))
            return {"ok": True, "recovery": recovery, "verification": settings.hosted}
        if action == "login":
            token, _ = store.login(email, data.get("password"))
            response = JSONResponse({"ok": True})
            response.set_cookie(
                COOKIE, token, max_age=43200, httponly=True, secure=settings.hosted, samesite="strict"
            )
            return response
        if action in ("forgot", "resend"):
            if not settings.hosted:
                raise ValueError("本机账户请使用恢复密钥")
            user = store.user(email)
            purpose = "reset" if action == "forgot" else "verify"
            if user and (
                (action == "forgot" and user["verified"]) or (action == "resend" and not user["verified"])
            ):
                settings.send(email, purpose, store.issue(user["id"], purpose))
            return {"ok": True}
        if action in ("verify", "reset"):
            store.redeem(str(data.get("token", "")), action, data.get("password"))
            return {"ok": True}
        if action == "recover":
            if settings.hosted:
                raise HTTPException(404, "未找到")
            return {
                "ok": True,
                "recovery": store.recover_local(email, str(data.get("key", "")), data.get("password")),
            }
        if action == "password":
            user = session(request)
            if not secrets.compare_digest(user["csrf"], request.headers.get("x-csrf-token", "")):
                raise HTTPException(403, "会话校验失败")
            store.change_password(user["id"], data.get("old"), data.get("password"))
            return {"ok": True}

    @app.get("/api/bootstrap")
    def start(request: Request):
        user = session(request)
        value = bootstrap()
        value["directory"] = ""
        value["account"] = {**user, "hosted": settings.hosted}
        return value

    @app.get("/api/projects")
    def projects(request: Request):
        return {"projects": store.projects(session(request)["id"])}

    @app.get("/api/projects/{pid}")
    def project(pid: str, request: Request):
        value = store.project(session(request)["id"], pid)
        if value is None:
            raise HTTPException(404, "项目不存在")
        return {"project": value}

    @app.post("/api/{action}")
    async def operation(action: str, request: Request):
        user = session(request)
        data = await body(request)
        if not store.limit("compute:" + user["id"], 120, 60):
            raise HTTPException(429, "请求过于频繁")
        if not capacity.acquire(blocking=False):
            raise HTTPException(429, "计算服务忙，请稍后重试")
        try:
            value = await run_in_threadpool(operate, action, data, user)
            if isinstance(value, tuple):
                return Response(
                    value[0],
                    media_type=value[1],
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(value[2])},
                )
            return JSONResponse(value)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from None
        except HTTPException:
            raise
        except Exception:
            LOGGER.exception("Account operation failed: %s", action)
            raise HTTPException(500, "操作未完成，请重试") from None
        finally:
            capacity.release()

    def operate(action, data, user):
        if action == "save":
            project = project_from_dict(data["project"]).to_dict()
            store.save(user["id"], project)
            return {"ok": True, "id": project["project_id"]}
        if action in ("diagnostics", "save-bundle"):
            raise HTTPException(403, "此入口不提供服务器文件操作，请使用导出")
        allowed = {"import", "calculate", "scan", "export"}
        if action not in allowed and not action.startswith("lab-"):
            raise HTTPException(404, "未找到")
        return isolated_dispatch(action, data) if settings.hosted else dispatch(action, data)

    @app.get("/{path:path}")
    def asset(path: str, request: Request):
        if path in ("", "workbench"):
            logged_in = store.session(request.cookies.get(COOKIE, ""))
            if path == "workbench" and not logged_in:
                return RedirectResponse("/", 303)
            name = "index.html" if path == "workbench" else "auth.html"
        else:
            name = ASSETS.get("/" + path) or ({"auth.js": "auth.js", "auth.css": "auth.css"}.get(path))
        if not name:
            raise HTTPException(404, "未找到")
        return Response(
            root.joinpath(name).read_bytes(), media_type=mimetypes.guess_type(name)[0] or "text/plain"
        )

    return app


def main():
    import uvicorn

    settings = Settings.environment()
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0" if settings.hosted else "127.0.0.1",
        port=int(os.getenv("PORT", "8510")),
        access_log=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
