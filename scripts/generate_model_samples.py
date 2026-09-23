"""Generate reproducible synthetic inputs and model-analysis evidence, never external references."""

import json
from pathlib import Path

from rf_link_calculator.advanced.api import dispatch
from rf_link_calculator.advanced.benchmark import handoff
from rf_link_calculator.advanced.data import import_dataset
from rf_link_calculator.advanced.examples import examples
from rf_link_calculator.domain.codec import project_from_dict
from rf_link_calculator.engine import calculate
from rf_link_calculator.examples import examples as scalar_examples
from rf_link_calculator.persistence.json_io import atomic_write, dumps

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))


def dataset_text(ds):
    if ds["kind"] == "sparameter":
        lines = ["# Hz S RI R 50"]
        for f, re, im in zip(ds["frequency_hz"], ds["s_real"], ds["s_imag"]):
            values = [f]
            for i, j in ((0, 0), (1, 0), (0, 1), (1, 1)):
                values.extend((re[i][j], im[i][j]))
            lines.append(" ".join(repr(v) for v in values))
        n = ds.get("noise")
        if n:
            for f, nf, mag, phase, rn in zip(
                n["frequency_hz"], n["nfmin_db"], n["gamma_mag"], n["gamma_deg"], n["rn_ohm"]
            ):
                lines.append(" ".join(repr(v) for v in (f, nf, mag, phase, rn / 50)))
        return "\n".join(lines) + "\n"
    columns = {
        "frequency": ["frequency_hz", "gain_db", "nf_db", "phase_deg", "iip3_dbm", "ip1_dbm"],
        "ampm": ["pin_dbm", "pout_dbm", "phase_deg"],
        "imt": ["m", "n", "output_dbm"],
        "iq": ["i_in", "q_in", "i_out", "q_out"],
    }[ds["kind"]]
    return (
        ",".join(columns)
        + "\n"
        + "".join(",".join(repr(float(v)) for v in row) + "\n" for row in zip(*(ds[k] for k in columns)))
    )


def main():
    inputs = ROOT / "examples/models"
    outputs = ROOT / "samples/model-validation"
    inputs.mkdir(parents=True, exist_ok=True)
    outputs.mkdir(parents=True, exist_ok=True)
    requests = {
        "sweep": {"action": "sweep", "parameters": {"low": 80e6, "high": 120e6, "count": 201}},
        "power": {"action": "power", "parameters": {"low": -40, "high": 0, "count": 161}},
        "spectrum": {"action": "spectrum", "parameters": {"count": 4096, "oversampling": 2}},
        "convergence": {"action": "convergence", "parameters": {"count": 4096, "oversampling": 2}},
        "imt": {"action": "spectrum", "parameters": {"mode": "imt"}},
        "waveform": {
            "action": "waveform",
            "parameters": {"sample_rate_hz": 10e6, "bandwidth_hz": 1e6, "spacing_hz": 2e6},
        },
        "noise": {"action": "noise", "parameters": {"low": 99.5e6, "high": 100.5e6}},
        "image": {"action": "image", "parameters": {"image_gain_db": -10, "image_temperature_k": 290}},
    }
    cases = {
        "扫频链路": ["sweep", "noise"],
        "失配链路": ["sweep"],
        "功率曲线链路": ["power", "spectrum", "convergence"],
        "混频杂散链路": ["imt", "image"],
        "宽带记忆链路": ["waveform"],
    }
    for key, request in requests.items():
        write_json(inputs / f"{key}-request.json", request)
    for name, p in examples().items():
        data = p.to_dict()
        lab = data["lab"]
        datasets = [
            (slots, key)
            for slots in lab["models"].values()
            for key, ds in slots.items()
            if ds["kind"] != "memory"
        ]
        if "iq" in lab:
            datasets.append((lab, "iq"))
        for owner, key in datasets:
            ds = owner[key]
            text = dataset_text(ds)
            filename = ds["metadata"]["filename"]
            atomic_write(inputs / filename, text.encode("utf-8"))
            owner[key] = import_dataset(ds["kind"], text, filename, ds["metadata"])
        p = project_from_dict(data)
        atomic_write(inputs / f"{name}.json", dumps(p).encode("utf-8"))
        write_json(outputs / f"{name}-calculate.json", calculate(p).to_dict())
        for case in cases[name]:
            request = requests[case]
            write_json(outputs / f"{name}-{case}.json", dispatch(request["action"], p, request["parameters"]))
            analysis = {"kind": request["action"], "parameters": request["parameters"]}
            atomic_write(outputs / f"{name}-{case}-对标.zip", handoff(p, analysis))
    atomic_write(outputs / "标量链路-MATLAB对标.zip", handoff(scalar_examples()["接收链路"]))
    atomic_write(
        inputs / "README.md",
        "# 合成模型工程\n\n五套工程及数据均为可复现的构造数据，不代表真实器件或商业软件结果。用 `scripts/generate_model_samples.py` 重新生成；请求文件由 `scripts/rf_lab.py` 执行。IQ 的功率单位为 √mW。S 参数文件含完整噪声参数。\n".encode(
            "utf-8"
        ),
    )
    print(
        json.dumps(
            {
                "projects": len(cases),
                "analyses": sum(map(len, cases.values())),
                "inputs": str(inputs),
                "outputs": str(outputs),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
