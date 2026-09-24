"""Single-instance account/project storage. All project queries are owner-scoped."""

import hashlib
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

HASHER = PasswordHasher()
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(32))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def email_address(value):
    value = str(value).strip().lower()
    if len(value) > 254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,63}", value):
        raise ValueError("请填写有效邮箱")
    return value


def password_hash(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 128:
        raise ValueError("密码长度需为 12～128 位")
    return HASHER.hash(value)


def verify_password(encoded, value):
    if not isinstance(value, str) or len(value) > 128:
        return False
    try:
        return HASHER.verify(encoded, value)
    except (VerificationError, InvalidHashError):
        return False


class Accounts:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
                    verified INTEGER NOT NULL, recovery TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    csrf TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS tokens (
                    token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    purpose TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS auth_codes (
                    email TEXT NOT NULL, purpose TEXT NOT NULL, code TEXT NOT NULL, expires REAL NOT NULL,
                    PRIMARY KEY(email,purpose));
                CREATE TABLE IF NOT EXISTS projects (
                    owner TEXT NOT NULL REFERENCES users(id), id TEXT NOT NULL,
                    name TEXT NOT NULL, link TEXT NOT NULL, body TEXT NOT NULL, updated REAL NOT NULL,
                    PRIMARY KEY(owner,id));
                CREATE TABLE IF NOT EXISTS limits (
                    key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def limit(self, key, maximum=10, seconds=900):
        now = time.time()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM limits WHERE expires<?", (now,))
            key = digest(key)
            row = db.execute("SELECT count FROM limits WHERE key=?", (key,)).fetchone()
            if row and row["count"] >= maximum:
                return False
            db.execute(
                """INSERT INTO limits VALUES(?,1,?) ON CONFLICT(key)
                DO UPDATE SET count=count+1""",
                (key, now + seconds),
            )
            return True

    def register(self, email, password, verified=False):
        email, encoded = email_address(email), password_hash(password)
        uid, recovery = secrets.token_hex(16), secrets.token_urlsafe(32)
        try:
            with self.db() as db:
                db.execute(
                    "INSERT INTO users VALUES(?,?,?,?,?,?)",
                    (uid, email, encoded, int(verified), digest(recovery) if verified else None, time.time()),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("无法注册此邮箱，请登录或找回密码") from exc
        return uid, recovery if verified else None

    def user(self, email):
        with self.db() as db:
            row = db.execute("SELECT * FROM users WHERE email=?", (email_address(email),)).fetchone()
            return dict(row) if row else None

    def login(self, email, password):
        user = self.user(email)
        valid = verify_password(user["password"] if user else DUMMY_HASH, password)
        if not user or not valid or not user["verified"]:
            raise ValueError("邮箱或密码不正确，或邮箱尚未验证")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?)", (digest(token), user["id"], csrf, time.time() + 43200)
            )
        return token, csrf

    def session(self, token):
        with self.db() as db:
            row = db.execute(
                """SELECT u.id,u.email,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token=? AND s.expires>? AND u.verified=1""",
                (digest(token), time.time()),
            ).fetchone()
            return dict(row) if row else None

    def logout(self, token):
        with self.db() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (digest(token),))

    def issue(self, uid, purpose):
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute(
                "DELETE FROM tokens WHERE expires<? OR (user_id=? AND purpose=?)", (time.time(), uid, purpose)
            )
            db.execute(
                "INSERT INTO tokens VALUES(?,?,?,?)", (digest(token), uid, purpose, time.time() + 1800)
            )
        return token

    def issue_code(self, email, purpose):
        email = email_address(email)
        code = f"{secrets.randbelow(1000000):06d}"
        with self.db() as db:
            db.execute("DELETE FROM auth_codes WHERE expires<?", (time.time(),))
            db.execute(
                """INSERT INTO auth_codes VALUES(?,?,?,?) ON CONFLICT(email,purpose)
                DO UPDATE SET code=excluded.code,expires=excluded.expires""",
                (email, purpose, digest(code), time.time() + 600),
            )
        return code

    def redeem_code(self, email, purpose, code):
        email = email_address(email)
        if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code.strip()):
            raise ValueError("验证码不正确")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT code FROM auth_codes WHERE email=? AND purpose=? AND expires>?",
                (email, purpose, time.time()),
            ).fetchone()
            if not row or not secrets.compare_digest(row["code"], digest(code.strip())):
                raise ValueError("验证码不正确或已失效")
            db.execute("DELETE FROM auth_codes WHERE email=? AND purpose=?", (email, purpose))

    def redeem(self, token, purpose, password=None):
        encoded = password_hash(password) if purpose == "reset" else None
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT user_id FROM tokens WHERE token=? AND purpose=? AND expires>?",
                (digest(token), purpose, time.time()),
            ).fetchone()
            if not row:
                raise ValueError("链接已失效，请重新申请")
            if purpose == "verify":
                db.execute("UPDATE users SET verified=1 WHERE id=?", (row["user_id"],))
            else:
                db.execute("UPDATE users SET password=? WHERE id=?", (encoded, row["user_id"]))
                db.execute("DELETE FROM sessions WHERE user_id=?", (row["user_id"],))
            db.execute("DELETE FROM tokens WHERE user_id=?", (row["user_id"],))

    def recover_local(self, email, key, password):
        user = self.user(email)
        if not user or not user["recovery"] or not secrets.compare_digest(user["recovery"], digest(key)):
            raise ValueError("邮箱或恢复密钥不正确")
        encoded, recovery = password_hash(password), secrets.token_urlsafe(32)
        with self.db() as db:
            cursor = db.execute(
                "UPDATE users SET password=?,recovery=? WHERE id=? AND recovery=?",
                (encoded, digest(recovery), user["id"], user["recovery"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("恢复密钥已失效")
            db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
            db.execute("DELETE FROM tokens WHERE user_id=?", (user["id"],))
        return recovery

    def change_password(self, uid, old, new):
        with self.db() as db:
            row = db.execute("SELECT password FROM users WHERE id=?", (uid,)).fetchone()
            if not row or not verify_password(row["password"], old):
                raise ValueError("当前密码不正确")
            db.execute("UPDATE users SET password=? WHERE id=?", (password_hash(new), uid))
            db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            db.execute("DELETE FROM tokens WHERE user_id=?", (uid,))

    def save(self, uid, project):
        raw = json.dumps(project, ensure_ascii=False, allow_nan=False)
        if len(raw.encode()) > 32 * 1024 * 1024:
            raise ValueError("项目大小超限")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            total = db.execute(
                "SELECT count(*),coalesce(sum(length(CAST(body AS BLOB))),0) FROM projects WHERE owner=? AND id<>?",
                (uid, project["project_id"]),
            ).fetchone()
            if total[0] >= 100 or total[1] + len(raw.encode()) > 256 * 1024 * 1024:
                raise ValueError("账户项目存储额度已满")
            db.execute(
                """INSERT INTO projects VALUES(?,?,?,?,?,?) ON CONFLICT(owner,id)
                DO UPDATE SET name=excluded.name,link=excluded.link,body=excluded.body,updated=excluded.updated""",
                (uid, project["project_id"], project["project_name"], project["link_name"], raw, time.time()),
            )

    def projects(self, uid):
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,name,link,updated FROM projects WHERE owner=? ORDER BY updated DESC", (uid,)
                )
            ]

    def project(self, uid, pid):
        with self.db() as db:
            row = db.execute("SELECT body FROM projects WHERE owner=? AND id=?", (uid, pid)).fetchone()
            return json.loads(row["body"]) if row else None
