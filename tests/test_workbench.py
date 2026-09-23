import json
import threading
from http.client import HTTPConnection
from io import BytesIO
from zipfile import ZipFile

import pytest

from rf_link_calculator.domain.codec import hashes
from rf_link_calculator.examples import examples
from rf_link_calculator.persistence.csv_io import export_csv
from rf_link_calculator.presentation.server import Handler, WorkbenchServer, dispatch


@pytest.fixture
def web_server():
    server = WorkbenchServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def request(server, path, data=None, origin=True, host=None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=15)
    headers = {}
    if host:
        headers["Host"] = host
    if data is not None:
        headers["Content-Type"] = "application/json"
        if origin:
            headers["Origin"] = f"http://127.0.0.1:{server.server_port}" if origin is True else origin
    connection.request(
        "POST" if data is not None else "GET", path, json.dumps(data) if data is not None else None, headers
    )
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def test_workbench_serves_installed_assets_and_contract(web_server):
    status, headers, body = request(web_server, "/")
    assert status == 200
    assert b'lang="zh-CN"' in body
    assert b"/app.js" in body
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    for path, mime in (
        ("/style.css", "text/css"),
        ("/app.js", "javascript"),
        ("/advanced.js", "javascript"),
        ("/circuit.js", "javascript"),
        ("/icons.svg", "image/svg+xml"),
    ):
        status, headers, raw = request(web_server, path)
        assert status == 200 and mime in headers["Content-Type"] and len(raw) > 100
    status, _, raw = request(web_server, "/api/bootstrap")
    data = json.loads(raw)
    assert status == 200 and data["result"]["metrics"]["gain_db"]["value"] == 28
    assert len(data["examples"]) == 27
    assert "mixer" in data["symbols"] and data["symbols"]["mixer"]
    assert "source" in data["new_stage"]


def test_workbench_rejects_cross_origin_and_path_traversal(web_server):
    p = examples()["接收链路"].to_dict()
    assert request(web_server, "/api/calculate", {"project": p}, origin=False)[0] == 403
    assert request(web_server, "/api/calculate", {"project": p}, origin="https://example.org")[0] == 403
    assert request(web_server, "/api/bootstrap", host=f"example.org:{web_server.server_port}")[0] == 403
    assert request(web_server, "/../pyproject.toml")[0] == 404
    assert request(web_server, "/%2e%2e/pyproject.toml")[0] == 404


def test_workbench_calculation_and_missing_parameter_edit(web_server):
    p = examples()["接收链路"].to_dict()
    p["stages"][1]["gain_db"] = 23
    p["stages"][1]["noise"]["value_db"] = None
    status, _, raw = request(web_server, "/api/calculate", {"project": p})
    result = json.loads(raw)
    assert status == 200
    assert result["result"]["metrics"]["gain_db"]["value"] == 31
    assert result["result"]["metrics"]["nf_db"]["status"] == "unknown"
    p["analysis"]["noise_bandwidth_hz"] = 0
    status, _, raw = request(web_server, "/api/calculate", {"project": p})
    assert status == 400 and "noise_bandwidth_hz" in json.loads(raw)["error"]


def test_workbench_csv_and_json_import_keep_state():
    p = examples()["中文与特殊字符"]
    data = dispatch(
        "import", {"format": "csv", "text": export_csv(p).decode("utf-8-sig"), "project": p.to_dict()}
    )
    assert data["project"]["stages"][0]["name"] == p.stages[0].name
    assert data["result"]["project_hash"] == hashes(p)[0]
    with pytest.raises(ValueError):
        dispatch("import", {"format": "json", "text": '{"schema_version":"9.0.0"}'})


def test_workbench_save_and_export_stale_guard(tmp_path):
    p = examples()["接收链路"]
    data = {"project": p.to_dict(), "directory": str(tmp_path), "project_hash": hashes(p)[0]}
    saved = dispatch("save", data)
    assert saved["path"].endswith(".json")
    assert len(list(tmp_path.glob("*.json"))) == 1
    raw, _, _ = dispatch("export", {**data, "kind": "zip"})
    with ZipFile(BytesIO(raw)) as z:
        assert "链路计算.xlsx" in z.namelist()
        assert json.loads(z.read("导出清单.json"))["status"] == "success"
    raw, mime, name = dispatch("export", {**data, "kind": "csv"})
    assert raw.startswith(b"\xef\xbb\xbf") and "text/csv" in mime and name.endswith(".csv")
    with pytest.raises(ValueError, match="先计算"):
        dispatch("export", {**data, "project_hash": "old"})
    result = dispatch("save-bundle", data)
    assert result["path"].endswith(".zip")


def test_workbench_scan_includes_true_nonlinear_output():
    p = examples()["累计压缩反例"]
    response = dispatch("scan", {"project": p.to_dict(), "low": -10, "high": 0})
    assert len(response["points"]) == 161
    assert response["points"][-1]["compressed"] == pytest.approx(-1.682084899, abs=1e-8)
    assert response["points"][-1]["linear"] == 0
    with pytest.raises(ValueError):
        dispatch("scan", {"project": p.to_dict(), "low": 1, "high": 0})
