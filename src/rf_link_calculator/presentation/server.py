"""Loopback-only workbench. Project state belongs to the browser tab, never a global server session."""

import argparse
import json
import logging
import mimetypes
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlsplit
from zipfile import ZipFile

import numpy as np

from rf_link_calculator.diagnostics import data_directory, diagnose
from rf_link_calculator.diagram.symbols import symbol
from rf_link_calculator.domain import labels
from rf_link_calculator.domain.codec import project_from_dict
from rf_link_calculator.domain.models import Project, Stage
from rf_link_calculator.engine import calculate
from rf_link_calculator.engine.compression import propagate
from rf_link_calculator.examples import examples
from rf_link_calculator.exporters.bundle import export_bundle, safe_name, save_bundle
from rf_link_calculator.persistence.csv_io import export_csv, import_csv
from rf_link_calculator.persistence.json_io import dumps, loads, save_project

LOGGER = logging.getLogger(__name__)
MAX_REQUEST = 64 * 1024 * 1024
ASSETS = {
    "/": "index.html",
    "/app.js": "app.js",
    "/advanced.js": "advanced.js",
    "/circuit.js": "circuit.js",
    "/style.css": "style.css",
    "/icons.svg": "icons.svg",
    "/login-hero-bg.png": "login-hero-bg.png",
    "/brand-alipay.svg": "brand-alipay.svg",
    "/brand-wechat.svg": "brand-wechat.svg",
    "/brand-github.svg": "brand-github.svg",
}


def calculated(project):
    result = calculate(project)
    return {"project": project.to_dict(), "result": result.to_dict()}


def dispatch(action: str, data: dict):
    """Pure request dispatch except explicitly named save/diagnostics operations."""
    if action == "import":
        if data.get("format") == "csv":
            project = import_csv(data["text"].encode("utf-8"), project_from_dict(data["project"]))
        else:
            project = loads(data["text"])
        return calculated(project)
    if action == "diagnostics":
        return diagnose(Path(data.get("directory") or data_directory()))
    project = project_from_dict(data["project"])
    if action.startswith("lab-"):
        from rf_link_calculator.advanced.api import dispatch as lab_dispatch

        return lab_dispatch(action.removeprefix("lab-"), project, data)
    if action == "calculate":
        return calculated(project)
    if action == "save":
        directory = Path(data.get("directory") or data_directory())
        path = save_project(
            project, directory / (safe_name(project.project_name + "_" + project.link_name) + ".json")
        )
        return {"path": str(path)}
    if action == "scan":
        low, high = float(data["low"]), float(data["high"])
        if not np.isfinite([low, high]).all() or not -10000 <= low < high <= 10000:
            raise ValueError("扫描范围应在 -10000～10000 dBm 内，且上限大于下限")
        result = calculate(project)
        gain = result.metrics["gain_db"].value
        modeled = project.lab and any(v.get("nonlinear") for v in project.lab.get("models", {}).values())
        if gain is None or (not modeled and result.metrics["ip1_dbm"].status not in ("estimated", "ideal")):
            raise ValueError("请补全增益与P1参数")
        if modeled:
            from rf_link_calculator.advanced.network import evaluate
            from rf_link_calculator.advanced.nonlinear import cw_chain
            from rf_link_calculator.advanced.spectral import require_matched

            require_matched(project)
            _, stages = evaluate(project)
            p = replace(project, stages=stages)
            points = []
            for x in np.linspace(low, high, 161):
                try:
                    y = cw_chain(p, float(x))[0]
                except ValueError:
                    y = None
                points.append({"x": float(x), "linear": float(x + gain), "compressed": y})
            return {"points": points}
        xs = np.linspace(low, high, 161)
        scan_stages = project.stages
        if project.lab and project.lab.get("models"):
            from rf_link_calculator.advanced.network import evaluate

            _, scan_stages = evaluate(project)
        return {
            "points": [
                {
                    "x": float(x),
                    "linear": float(x + gain),
                    "compressed": propagate(scan_stages, float(x), project.analysis.default_compression_p)[0],
                }
                for x in xs
            ]
        }
    if action in ("export", "save-bundle"):
        result = calculate(project)
        if data.get("project_hash") != result.project_hash:
            raise ValueError("输入已更改，请先计算")
        kind = data.get("kind", "zip")
        if kind == "json":
            return dumps(project).encode("utf-8"), "application/json", safe_name(project.link_name) + ".json"
        if kind == "csv":
            if project.lab and project.lab.get("models"):
                raise ValueError("器件表无法保存模型数据，请导出项目文件或工程包")
            return export_csv(project), "text/csv;charset=utf-8", safe_name(project.link_name) + ".csv"
        raw, manifest = export_bundle(project, result, strict=bool(data.get("strict")))
        if action == "save-bundle":
            path = save_bundle(raw, Path(data.get("directory") or data_directory()), project)
            return {"path": str(path), "manifest": manifest}
        if kind == "zip":
            return raw, "application/zip", safe_name(project.link_name) + "_工程包.zip"
        names = {"xlsx": "链路计算.xlsx", "svg": "链路框图.svg", "drawio": "链路框图.drawio"}
        if kind not in names:
            raise ValueError("不支持的导出格式")
        with ZipFile(BytesIO(raw)) as archive:
            if names[kind] not in archive.namelist():
                raise ValueError("该格式生成失败，请导出工程包查看清单")
            return (
                archive.read(names[kind]),
                "application/octet-stream",
                safe_name(project.link_name) + "." + kind,
            )
    raise ValueError("未知操作")


def bootstrap():
    ex = examples()
    from rf_link_calculator.advanced.examples import examples as model_examples

    ex.update(model_examples())
    from rf_link_calculator.advanced.harmonic_examples import examples as harmonic_examples

    ex.update(harmonic_examples())
    from rf_link_calculator.advanced.circuit_examples import examples as circuit_examples

    ex.update(circuit_examples())
    from rf_link_calculator.advanced.reference_cases import examples as reference_examples

    ex.update(reference_examples())
    return {
        **calculated(ex["接收链路"]),
        "examples": {name: p.to_dict() for name, p in ex.items()},
        "new_project": Project(stages=()).to_dict(),
        "new_stage": asdict(Stage()),
        "labels": {
            k: getattr(labels, k)
            for k in (
                "TYPES",
                "LINK_TYPES",
                "NOISE",
                "POWER",
                "P1REF",
                "IP3REF",
                "METRICS",
                "STATUS",
            )
        },
        "symbols": {k: [asdict(p) for p in symbol(k)] for k in {*labels.TYPES, "lo"}},
        "directory": str(data_directory()),
    }


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    server_version = "RFLink/2.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, fmt, *args):
        LOGGER.info(fmt, *args)

    def reply(self, body, status=200, mime="application/json; charset=utf-8", name=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        )
        if name:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(name))
        self.end_headers()
        self.wfile.write(body)

    def allowed_host(self):
        port = self.server.server_port
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self):
        if not self.allowed_host():
            return self.reply({"error": "仅允许本机访问"}, 403)
        path = urlsplit(self.path).path
        if path in ASSETS:
            name = ASSETS[path]
            content = files("rf_link_calculator").joinpath("presentation", "web", name).read_bytes()
            return self.reply(
                content, mime=(mimetypes.guess_type(name)[0] or "text/plain") + "; charset=utf-8"
            )
        if path in ("/health", "/_stcore/health"):
            return self.reply({"status": "ok", "ui": "workbench"})
        if path == "/api/bootstrap":
            return self.reply(bootstrap())
        return self.reply({"error": "未找到"}, 404)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REQUEST:
                return self.reply({"error": "文件过大或请求为空"}, 413)
            raw = self.rfile.read(length)
            # Consume the bounded body before rejecting, avoiding a Windows TCP reset
            # that would otherwise hide the useful 403 response from the caller.
            expected = f"http://{self.headers.get('Host')}"
            if not self.allowed_host() or self.headers.get("Origin") != expected:
                return self.reply({"error": "请求来源无效"}, 403)
            if self.headers.get_content_type() != "application/json":
                return self.reply({"error": "请求格式无效"}, 415)
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("请求必须是对象")
            path = urlsplit(self.path).path
            if not path.startswith("/api/"):
                return self.reply({"error": "未找到"}, 404)
            value = dispatch(path.removeprefix("/api/"), data)
            if isinstance(value, tuple):
                return self.reply(value[0], mime=value[1], name=value[2])
            return self.reply(value)
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            return self.reply({"error": str(exc)}, 400)
        except OSError as exc:
            return self.reply({"error": "文件操作失败：" + str(exc)}, 400)
        except Exception:
            LOGGER.exception("Workbench request failed")
            return self.reply({"error": "操作未完成，请重试或查看运行日志"}, 500)


def main():
    parser = argparse.ArgumentParser(description="RF Link本地工作台")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    server = WorkbenchServer(("127.0.0.1", args.port), Handler)
    print(f"RF Link: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
