"""Windows entry point; no external Python installation is needed in the frozen build."""

import argparse
import json
import logging
import multiprocessing
import os
import socket
import sys
import threading
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", type=Path)
    parser.add_argument("--server-only", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    from rf_link_calculator import __version__
    from rf_link_calculator.portal import Settings, create_app

    directory = Path(
        os.getenv("RF_LINK_DATA_DIR", str(Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "RF Link"))
    )
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=directory / "desktop.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    if args.self_test:
        import tempfile

        from rf_link_calculator.accounts import Accounts
        from rf_link_calculator.presentation.server import bootstrap, dispatch

        config = bootstrap()
        project = config["examples"]["接收链路"]
        result = dispatch("calculate", {"project": project})
        raw, _, _ = dispatch(
            "export", {"project": project, "kind": "xlsx", "project_hash": result["result"]["project_hash"]}
        )
        with tempfile.TemporaryDirectory(dir=directory) as tmp:
            store = Accounts(Path(tmp) / "test.sqlite3")
            uid, _ = store.register("self-test@example.invalid", "Temporary-test-password-2026", True)
            token, _ = store.login("self-test@example.invalid", "Temporary-test-password-2026")
            assert store.session(token)["id"] == uid
            store.save(uid, project)
            assert store.project(uid, project["project_id"])
        assert abs(result["result"]["metrics"]["gain_db"]["value"] - 28) < 1e-9
        args.self_test.parent.mkdir(parents=True, exist_ok=True)
        args.self_test.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "version": __version__,
                    "examples": len(config["examples"]),
                    "gain_db": 28,
                    "xlsx_bytes": len(raw),
                    "accounts": True,
                    "frozen": bool(getattr(sys, "frozen", False)),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return
    import uvicorn

    listener = socket.socket()
    listener.bind(("127.0.0.1", args.port))
    listener.listen(128)
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(
        uvicorn.Config(create_app(Settings(directory)), log_config=None, access_log=False)
    )
    if args.server_only:
        server.run(sockets=[listener])
        return
    import webview

    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    webview.create_window("RF Link", url, width=1440, height=900, min_size=(1100, 700), maximized=True)
    try:
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(directory / "webview"))
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        main()
    except Exception:
        logging.exception("RF Link startup failed")
        if getattr(sys, "frozen", False):
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0,
                "启动失败，请检查 Microsoft Edge WebView2 Runtime，并查看用户数据目录中的 desktop.log。",
                "RF Link",
                16,
            )
        raise
