import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from rf_link_calculator.accounts import Accounts, digest
from rf_link_calculator.examples import examples
from rf_link_calculator.portal import Settings, create_app

PASSWORD = "Test-only-long-password-2026"


def test_account_credentials_reset_and_expiry(tmp_path):
    store = Accounts(tmp_path / "accounts.db")
    uid, recovery = store.register("Alice@example.com", PASSWORD, True)
    with store.db() as db:
        row = db.execute("SELECT * FROM users").fetchone()
        assert row["password"].startswith("$argon2id$") and row["password"] != PASSWORD
        assert row["recovery"] != recovery
    token, _ = store.login("alice@example.com", PASSWORD)
    assert store.session(token)["id"] == uid
    new_key = store.recover_local("alice@example.com", recovery, PASSWORD + "new")
    assert store.session(token) is None and new_key != recovery
    with pytest.raises(ValueError):
        store.recover_local("alice@example.com", recovery, PASSWORD)
    reset = store.issue(uid, "reset")
    store.redeem(reset, "reset", PASSWORD)
    with pytest.raises(ValueError):
        store.redeem(reset, "reset", PASSWORD)
    token, _ = store.login("alice@example.com", PASSWORD)
    with store.db() as db:
        db.execute("UPDATE sessions SET expires=? WHERE token=?", (time.time() - 1, digest(token)))
    assert store.session(token) is None


def test_pending_account_verification_and_password_change(tmp_path):
    store = Accounts(tmp_path / "accounts.db")
    uid, recovery = store.register("alice@example.com", PASSWORD)
    assert recovery is None
    with pytest.raises(ValueError):
        store.login("alice@example.com", PASSWORD)
    token = store.issue(uid, "verify")
    with pytest.raises(ValueError):
        store.redeem(token, "reset", PASSWORD)
    store.redeem(token, "verify")
    session, _ = store.login("alice@example.com", PASSWORD)
    store.change_password(uid, PASSWORD, PASSWORD + "changed")
    assert store.session(session) is None
    with pytest.raises(ValueError):
        store.login("alice@example.com", PASSWORD)


@pytest.fixture
def portal(tmp_path):
    app = create_app(Settings(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8510") as client:
        yield client, app.state.accounts


def post(client, path, data, csrf=None, origin="http://127.0.0.1:8510"):
    headers = {"Origin": origin}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    return client.post(path, json=data, headers=headers)


def login(client, email):
    assert post(client, "/auth/register", {"email": email, "password": PASSWORD}).status_code == 200
    response = post(client, "/auth/login", {"email": email, "password": PASSWORD})
    assert response.status_code == 200
    assert (
        "HttpOnly" in response.headers["set-cookie"] and "SameSite=strict" in response.headers["set-cookie"]
    )
    return client.get("/api/bootstrap").json()["account"]["csrf"]


def test_portal_isolation_same_project_id_and_path_override(portal):
    client, _ = portal
    assert client.get("/api/bootstrap").status_code == 401
    assert client.get("/workbench", follow_redirects=False).status_code == 303
    alice = login(client, "alice@example.com")
    project = examples()["接收链路"].to_dict()
    project["project_name"] = "Alice private"
    assert (
        post(client, "/api/save", {"project": project, "directory": "C:/Windows"}, alice).status_code == 200
    )
    pid = project["project_id"]
    assert client.get(f"/api/projects/{pid}").json()["project"]["project_name"] == "Alice private"
    post(client, "/auth/logout", {})
    bob = login(client, "bob@example.com")
    assert client.get("/api/projects").json()["projects"] == []
    assert client.get(f"/api/projects/{pid}").status_code == 404
    project["project_name"] = "Bob private"
    assert post(client, "/api/save", {"project": project}, bob).status_code == 200
    for action in ("save-bundle", "diagnostics"):
        assert (
            post(client, "/api/" + action, {"project": project, "directory": "C:/Windows"}, bob).status_code
            == 403
        )
    post(client, "/auth/logout", {})
    post(client, "/auth/login", {"email": "alice@example.com", "password": PASSWORD})
    assert client.get(f"/api/projects/{pid}").json()["project"]["project_name"] == "Alice private"


def test_portal_csrf_rate_limits_invalid_bodies_and_calculation(portal):
    client, _ = portal
    csrf = login(client, "alice@example.com")
    project = examples()["接收链路"].to_dict()
    assert post(client, "/api/calculate", {"project": project}).status_code == 403
    assert (
        post(client, "/api/calculate", {"project": project}, csrf, "https://evil.example").status_code == 403
    )
    assert client.get("/health", headers={"host": "evil.example"}).status_code == 403
    result = post(client, "/api/calculate", {"project": project}, csrf).json()
    assert result["result"]["metrics"]["gain_db"]["value"] == 28
    raw = post(
        client,
        "/api/export",
        {"project": project, "kind": "json", "project_hash": result["result"]["project_hash"]},
        csrf,
    )
    assert raw.status_code == 200 and raw.json()["project_id"] == project["project_id"]
    for _ in range(12):
        response = post(client, "/auth/login", {"email": "alice@example.com", "password": "wrong"})
    assert response.status_code == 429
    assert (
        post(client, "/auth/register", {"email": "huge@example.com", "password": "x" * 17000}).status_code
        == 413
    )


def test_hosted_settings_and_mail_flow(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError):
        create_app(Settings(tmp_path, hosted=True))
    sent = []
    monkeypatch.setattr(
        Settings, "send", lambda self, email, purpose, token: sent.append((email, purpose, token))
    )
    app = create_app(
        Settings(tmp_path, True, "https://rf.example.com", "smtp.example.com", mail_from="rf@example.com")
    )
    with TestClient(app, base_url="https://rf.example.com") as client:

        def submit(action, data):
            return post(client, "/auth/" + action, data, origin="https://rf.example.com")

        assert submit("register", {"email": "alice@example.com", "password": PASSWORD}).json()["verification"]
        assert submit("login", {"email": "alice@example.com", "password": PASSWORD}).status_code == 400
        assert submit("verify", {"token": sent[-1][2]}).status_code == 200
        assert (
            "Secure"
            in submit("login", {"email": "alice@example.com", "password": PASSWORD}).headers["set-cookie"]
        )
        assert submit("forgot", {"email": "alice@example.com"}).status_code == 200
        reset = sent[-1][2]
        assert submit("reset", {"token": reset, "password": PASSWORD + "changed"}).status_code == 200
        assert client.get("/api/bootstrap").status_code == 401
        assert submit("reset", {"token": reset, "password": PASSWORD}).status_code == 400
        assert (
            submit(
                "recover", {"email": "alice@example.com", "key": "invalid", "password": PASSWORD}
            ).status_code
            == 404
        )


def test_database_persistence_and_quota(tmp_path):
    store = Accounts(tmp_path / "accounts.db")
    uid, _ = store.register("alice@example.com", PASSWORD, True)
    project = examples()["接收链路"].to_dict()
    store.save(uid, project)
    assert Accounts(store.path).project(uid, project["project_id"]) == json.loads(json.dumps(project))
    assert store.limit("quota", 1)
    assert not store.limit("quota", 1)
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_hosted_process_calculation_and_timeout_cleanup():
    import multiprocessing

    from rf_link_calculator.portal import isolated_dispatch

    project = examples()["接收链路"].to_dict()
    result = isolated_dispatch("calculate", {"project": project}, timeout=30)
    assert result["result"]["metrics"]["gain_db"]["value"] == 28
    before = {child.pid for child in multiprocessing.active_children()}
    with pytest.raises(ValueError, match="超时"):
        isolated_dispatch("calculate", {"project": project}, timeout=0)
    assert {child.pid for child in multiprocessing.active_children()} == before
