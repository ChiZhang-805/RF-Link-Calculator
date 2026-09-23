"""Generate portable transistor/periodic-noise inputs, results and handoff files."""

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from rf_link_calculator import __version__
from rf_link_calculator.advanced.api import dispatch
from rf_link_calculator.advanced.benchmark import handoff
from rf_link_calculator.advanced.circuit_examples import examples
from rf_link_calculator.persistence.json_io import dumps

ROOT = Path(__file__).resolve().parents[1]


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), "utf-8")


def main():
    inputs, output = ROOT / "examples/circuits", ROOT / "samples/circuit-validation"
    inputs.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    report = {"version": __version__, "source": "synthetic", "cases": {}}
    for name, project in examples().items():
        (inputs / (name + ".json")).write_text(dumps(project), "utf-8")
        write(inputs / (name + "-电路.json"), project.lab["circuit"])
        for action in ("circuit-dc", "circuit-hb-convergence", "circuit-noise-convergence"):
            parameters = {} if action == "circuit-dc" else {"fundamental_hz": 1e6, "harmonics": 8}
            if "noise" in action:
                parameters.update(offset_hz=1e4, sidebands=8, output_harmonics=1)
            key = name + "-" + action
            analysis = {"kind": action, "parameters": parameters}
            write(inputs / (key + "-request.json"), analysis)
            result = dispatch(action, project, parameters)
            write(output / (key + ".json"), result)
            (output / (key + "-对标.zip")).write_bytes(handoff(project, analysis))
            report["cases"][key] = {
                k: result[k]
                for k in ("calculation_hash", "solver", "convergence", "stability", "scope")
                if k in result
            }
    write(ROOT / "docs/晶体管与周期噪声验证.json", report)
    archive = ROOT / f"dist/RF-Link-Calculator-{__version__}-circuits.zip"
    archive.parent.mkdir(exist_ok=True)
    with ZipFile(archive, "w", ZIP_DEFLATED) as z:
        for directory in (inputs, output):
            for path in sorted(directory.rglob("*")):
                if path.is_file() and path.suffix != ".log":
                    z.write(path, path.relative_to(ROOT).as_posix())
        for path in (ROOT / "docs").glob("晶体管*"):
            z.write(path, path.relative_to(ROOT).as_posix())
    print(json.dumps({"cases": len(report["cases"]), "archive": str(archive)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
