"""Build portable HB/noise examples and an independent trigonometric reference."""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from rf_link_calculator.advanced import benchmark
from rf_link_calculator.advanced.api import dispatch
from rf_link_calculator.advanced.data import import_dataset
from rf_link_calculator.advanced.harmonic_examples import examples
from rf_link_calculator.domain.codec import hashes
from rf_link_calculator.persistence.json_io import atomic_write, dumps

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))


def main():
    inputs, output = ROOT / "examples/harmonics", ROOT / "samples/harmonic-validation"
    inputs.mkdir(exist_ok=True, parents=True)
    output.mkdir(exist_ok=True, parents=True)
    report = {}
    tones = [{"frequency_hz": f, "power_dbm": -30} for f in (9e6, 11e6)]
    for name, project in examples().items():
        atomic_write(inputs / (name + ".json"), dumps(project).encode("utf-8"))
        action = "image-auto" if name == "镜像噪声链路" else "harmonic-convergence"
        parameters = (
            {} if action == "image-auto" else {"fundamental_hz": 1e6, "harmonics": 64, "tones": tones}
        )
        request = {"action": action, "parameters": parameters}
        save(inputs / (name + "-request.json"), request)
        result = dispatch(action, project, parameters)
        save(output / (name + "-结果.json"), result)
        analysis = {"kind": action, "parameters": parameters}
        atomic_write(output / (name + "-对标.zip"), benchmark.handoff(project, analysis))
        report[name] = {
            key: result[key]
            for key in ("solver", "convergence", "nf_db", "image_gain_db", "calculation_hash")
            if key in result
        }
        for spec in project.lab["models"].values():
            if "harmonic" not in spec:
                continue
            model = spec["harmonic"]
            text = "order,coefficient\n" + "".join(
                f"{i},{c}\n" for i, c in enumerate(model["coefficients"], 1)
            )
            atomic_write(inputs / model["metadata"]["filename"], text.encode("utf-8"))
    project = examples()["谐波反馈链路"]
    stage = replace(project.stages[0], id="analytic", name="解析放大器", gain_db=20 * np.log10(2))
    model = import_dataset(
        "harmonic",
        "order,coefficient\n1,2\n2,0.1\n3,-0.2\n",
        "analytic.csv",
        {"source": "synthetic", "max_input": 1},
    )
    project = replace(
        project,
        project_id="解析双音谐波",
        project_name="解析双音谐波",
        link_name="解析双音谐波",
        stages=(stage,),
        lab={
            "models": {stage.id: {"harmonic": model}},
            "settings": {"hb_fundamental_hz": 1e6, "hb_harmonics": 64},
        },
    )
    analysis = {"kind": "harmonic", "parameters": {"fundamental_hz": 1e6, "harmonics": 64, "tones": tones}}
    # Fourier coefficients from cos^2/cos^3 identities, independent of the HB solver.
    a = np.sqrt(2 * 10 ** (-30 / 10))
    expected = {
        0: 0.1 * a * a,
        9: (2 * a - 9 * 0.2 * a**3 / 4) / 2,
        11: (2 * a - 9 * 0.2 * a**3 / 4) / 2,
        2: 0.1 * a * a / 2,
        20: 0.1 * a * a / 2,
        18: 0.1 * a * a / 4,
        22: 0.1 * a * a / 4,
        7: -3 * 0.2 * a**3 / 8,
        13: -3 * 0.2 * a**3 / 8,
        27: -0.2 * a**3 / 8,
        33: -0.2 * a**3 / 8,
        29: -3 * 0.2 * a**3 / 8,
        31: -3 * 0.2 * a**3 / 8,
    }
    reference = []
    for f, value in sorted(expected.items()):
        power = (1 if f == 0 else 2) * value * value
        for metric, number, unit in (
            ("power_dbm", 10 * np.log10(power), "dBm"),
            ("phase_deg", 0 if value > 0 else 180, "deg"),
            ("wave_real", value, "sqrt_mW"),
            ("wave_imag", 0, "sqrt_mW"),
        ):
            reference.append(
                {
                    "scope": "frequency:" + format(f * 1e6, ".12g"),
                    "metric": metric,
                    "value": format(number, ".15g"),
                    "unit": unit,
                    "status": "valid",
                }
            )
    data = {
        "kind": "reference",
        "rows": reference,
        "metadata": {
            "software": "三角恒等式解析参考",
            "version": "1",
            "solver": "cosine polynomial identities",
            "source": "synthetic",
            "analysis": analysis,
            "calculation_hash": hashes(project)[1],
        },
    }
    result = benchmark.compare(project, data)
    assert result["status"] == "internal_only" and len(result["rows"]) == 52
    project = replace(project, lab={**project.lab, "reference": data})
    atomic_write(inputs / "解析双音谐波.json", dumps(project).encode("utf-8"))
    atomic_write(output / "解析参考.csv", benchmark.csv_text(reference).encode("utf-8-sig"))
    save(output / "解析参考对比.json", result)
    atomic_write(output / "解析双音谐波-对标.zip", benchmark.handoff(project, analysis))
    report["analytic"] = {
        "status": result["status"],
        "rows": len(result["rows"]),
        "error_summary": result["error_summary"],
    }
    save(output / "report.json", report)
    save(ROOT / "docs/谐波与镜像验证.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
