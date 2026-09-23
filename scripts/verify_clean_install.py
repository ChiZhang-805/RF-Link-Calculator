"""Install the built wheel in a fresh venv and exercise it outside the source tree."""

import json
import os
import subprocess
import sys
import time
import venv
from hashlib import sha256
from pathlib import Path

from rf_link_calculator import __version__

ROOT = Path(__file__).resolve().parents[1]

SMOKE = r"""
import json
import threading
from http.client import HTTPConnection
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
import rf_link_calculator
from rf_link_calculator.engine import calculate
from rf_link_calculator.examples import examples
from rf_link_calculator.exporters.bundle import export_bundle
from rf_link_calculator.persistence.json_io import dumps, loads
from rf_link_calculator.presentation.server import WorkbenchServer, Handler
from streamlit.testing.v1 import AppTest
p = examples()["接收链路"]
assert loads(dumps(p)) == p
r = calculate(p)
assert abs(r.metrics["gain_db"].value - 28) < 1e-9
raw, manifest = export_bundle(p, r)
assert manifest["status"] == "success"
with ZipFile(BytesIO(raw)) as z:
    assert "链路计算.xlsx" in z.namelist()
    assert z.testzip() is None
at = AppTest.from_string("from rf_link_calculator.presentation.app import run\nrun()")
at.run(timeout=30)
assert len(at.exception) == 0, str(at.exception)
assert "site-packages" in str(rf_link_calculator.__file__)
server = WorkbenchServer(("127.0.0.1", 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
assets = {}
try:
    for path in ('/', '/app.js', '/advanced.js', '/circuit.js', '/style.css', '/icons.svg', '/api/bootstrap'):
        connection = HTTPConnection('127.0.0.1', server.server_port)
        connection.request('GET', path)
        response = connection.getresponse()
        body = response.read()
        assert response.status == 200 and body
        assets[path] = len(body)
        if path == '/api/bootstrap':
            assert json.loads(body)['result']['metrics']['gain_db']['value'] == 28
        connection.close()
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
from rf_link_calculator.advanced.examples import examples as model_examples
from rf_link_calculator.advanced.api import dispatch as lab_dispatch
model_projects = model_examples()
for model_project in model_projects.values():
    assert loads(dumps(model_project)) == model_project
    assert calculate(model_project).metrics['gain_db'].value is not None
wave = lab_dispatch('waveform', model_projects['宽带记忆链路'], {'sample_rate_hz':1e7, 'bandwidth_hz':1e6, 'spacing_hz':2e6})
assert wave['validation']['nmse_db'] < -100
from rf_link_calculator.advanced.reference_cases import examples as published_examples
from rf_link_calculator.advanced.benchmark import compare
published = published_examples()
assert all(compare(p,p.lab['reference'])['status']=='published_match' for p in published.values())
from rf_link_calculator.advanced.harmonic_examples import examples as harmonic_examples
harmonic_projects = harmonic_examples()
for name,p in harmonic_projects.items():
    assert loads(dumps(p)) == p
    if name == '镜像噪声链路':
        assert lab_dispatch('image-auto',p,{})['nf_db'] > 0
    else:
        assert lab_dispatch('harmonic',p,{'fundamental_hz':1e6,'harmonics':64})['solver']['status'] == 'converged'
from rf_link_calculator.advanced.circuit_examples import examples as circuit_examples
circuit_projects = circuit_examples()
for p in circuit_projects.values():
    assert loads(dumps(p)) == p
    assert lab_dispatch('circuit-dc',p,{})['solver']['status']=='converged'
    args={'fundamental_hz':1e6,'harmonics':8,'offset_hz':1e4,'sidebands':8,'output_harmonics':1}
    assert lab_dispatch('circuit-noise-convergence',p,args)['convergence']['status']=='pass'
print(json.dumps({"package_path": str(rf_link_calculator.__file__),
    "version": rf_link_calculator.__version__, "workbench_assets": assets,
    "gain_db": r.metrics["gain_db"].value, "export_status": manifest["status"],
    "ui_exceptions": len(at.exception), "example_count": len(examples()),
    "advanced_example_count":len(model_projects), "published_example_count":len(published), "harmonic_example_count":len(harmonic_projects), "circuit_example_count":len(circuit_projects), "waveform_validation_nmse_db":wave['validation']['nmse_db']}, ensure_ascii=False))
"""


def main():
    target = ROOT / "artifacts" / f"clean-install-{time.time_ns()}"
    target.mkdir(parents=True)
    runtime = target / "venv"
    report = {"status": "running", "venv": str(runtime), "python": sys.version}
    env = dict(os.environ, PYTHONUTF8="1")
    env.setdefault("PIP_CACHE_DIR", str(ROOT / "artifacts/pip-cache"))
    try:
        venv.EnvBuilder(with_pip=True).create(runtime)
        python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        wheel = ROOT / f"dist/rf_link_calculator-{__version__}-py3-none-any.whl"
        with (target / "install.log").open("w", encoding="utf-8") as log:
            for args in (
                ["-m", "pip", "install", "-r", str(ROOT / "requirements.txt")],
                ["-m", "pip", "install", "--no-deps", str(wheel)],
                ["-m", "pip", "check"],
            ):
                subprocess.run(
                    [str(python), *args],
                    cwd=target,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=600,
                )
        result = subprocess.run(
            [str(python), "-c", SMOKE],
            cwd=target,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=120,
        )
        (target / "smoke.log").write_text(result.stdout + result.stderr, "utf-8")
        report.update(json.loads(result.stdout.strip().splitlines()[-1]))
        report["wheel_sha256"] = sha256(wheel.read_bytes()).hexdigest()
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        if isinstance(exc, subprocess.CalledProcessError):
            (target / "failure.log").write_text(str(exc.stdout) + str(exc.stderr), "utf-8")
    (ROOT / "docs/独立安装验证.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
