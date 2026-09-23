"""Run scoped checks against manufacturer tables, published budgets and held-out measured IQ."""

import argparse
import csv
import hashlib
import json
from io import StringIO
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import skrf as rf
from fetch_public_validation import extract_tables, fetch_sources

from rf_link_calculator.advanced.benchmark import compare, csv_text, handoff
from rf_link_calculator.advanced.data import import_dataset
from rf_link_calculator.advanced.network import evaluate, s_at
from rf_link_calculator.advanced.nonlinear import apply_model, evm, fit_memory, wave_metrics
from rf_link_calculator.advanced.reference_cases import examples as published_examples
from rf_link_calculator.domain.models import Analysis, NoiseSpec, PowerSpec, Project, Stage
from rf_link_calculator.engine import calculate
from rf_link_calculator.persistence.json_io import atomic_write, dumps

ROOT = Path(__file__).resolve().parents[1]


def save_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))


def save_csv(path, rows):
    out = StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write(path, out.getvalue().encode("utf-8-sig"))


def summary(errors):
    a = np.asarray(errors, float)
    return {
        "count": len(a),
        "max_abs_error": float(np.max(abs(a))),
        "rms_error": float(np.sqrt(np.mean(a**2))),
    }


def manufacturer(cache, output):
    pages = extract_tables(cache / "PGA-103+_VIEW.pdf")
    source_url = "https://www.minicircuits.com/pages/s-params/PGA-103+_VIEW.pdf"
    comparisons, all_datasets = [], {}
    with ZipFile(cache / "PGA-103+_S2P.zip") as archive:
        for page in pages:
            voltage, temperature = page["voltage_v"], page["temperature_c"]
            stem = f"PGA-103+_{voltage:g}V_{'Minus' if temperature < 0 else 'Plus'}{abs(temperature):g}DegC"
            filename = stem + ".s2p"
            raw = archive.read(filename)
            metadata = {
                "source": "manufacturer",
                "manufacturer": "Mini-Circuits",
                "model": "PGA-103+",
                "voltage_v": voltage,
                "temperature_c": temperature,
                "pdf_page": page["page"],
                "url": source_url,
                "sparameter_url": "https://www.minicircuits.com/pages/s-params/PGA-103+_S2P.zip",
                "source_date": "2012-06-01",
            }
            ds = import_dataset("sparameter", raw.decode(), filename, metadata)
            all_datasets[filename] = ds
            frequency_rows = []
            for frequency, gain, isolation, rin, rout, _, _, oip3, op1, nf in page["rows"]:
                s = s_at(ds, frequency * 1e6)
                values = (
                    20 * np.log10(abs(s[1, 0])),
                    -20 * np.log10(abs(s[0, 1])),
                    -20 * np.log10(abs(s[0, 0])),
                    -20 * np.log10(abs(s[1, 1])),
                )
                for key, actual, reference in zip(
                    ("gain_db", "isolation_db", "input_return_loss_db", "output_return_loss_db"),
                    values,
                    (gain, isolation, rin, rout),
                ):
                    comparisons.append(
                        {
                            "condition": stem,
                            "frequency_hz": frequency * 1e6,
                            "metric": key,
                            "calculated": float(actual),
                            "published": reference,
                            "delta_db": float(actual - reference),
                            "tolerance_db": 0.0050001,
                            "status": "pass" if abs(actual - reference) <= 0.0050001 else "fail",
                        }
                    )
                frequency_rows.append(
                    {
                        "frequency_hz": frequency * 1e6,
                        "gain_db": gain,
                        "nf_db": nf,
                        "iip3_dbm": oip3 - gain,
                        "ip1_dbm": op1 - gain + 1,
                    }
                )
            save_csv(output / "tables" / (stem + ".csv"), frequency_rows)
            curve = import_dataset(
                "frequency",
                (output / "tables" / (stem + ".csv")).read_text("utf-8-sig"),
                stem + ".csv",
                metadata,
            )
            for mode, dataset in (("S参数", ds), ("频率预算", curve)):
                stage = Stage(
                    id="pga103",
                    name="PGA-103+",
                    type="lna",
                    gain_db=None,
                    noise=NoiseSpec("unknown"),
                    ip3=PowerSpec("unknown"),
                    p1db=PowerSpec("unknown"),
                    frequency_range_hz=(50e6, 4e9),
                    source={
                        "kind": "manufacturer",
                        "url": source_url,
                        "version": "2012-06-01",
                        "bias": f"{voltage:g} V / {page['current_ma']:g} mA",
                        "temperature_k": temperature + 273.15,
                        "specification": "typ",
                        "note": f"Mini-Circuits PGA-103+，官方典型数据第 {page['page']} 页",
                    },
                )
                p = Project(
                    schema_version="2.0.0",
                    project_id=stem + "-" + mode,
                    project_name="PGA-103+ 厂商数据",
                    link_name=f"PGA-103+ · {voltage:g}V · {temperature:g}°C · {mode}",
                    stages=(stage,),
                    analysis=Analysis(source_frequency_hz=1e9, input_power_dbm=-60),
                    lab={
                        "models": {stage.id: {"linear": dataset}},
                        "settings": {"sweep_low_hz": 50e6, "sweep_high_hz": 4e9},
                    },
                    notes="Mini-Circuits 公开典型数据。S文件不含噪声参数，不能据此计算失配NF。频率预算采用独立NF/IP3/P1表；P1仍是锚点估算，未获得AM/AM、AM/PM曲线。未对实际器件独立测量。",
                )
                atomic_write(output / "projects" / f"{stem}-{mode}.json", dumps(p).encode("utf-8"))
                save_json(output / "results" / f"{stem}-{mode}.json", calculate(p).to_dict())
    save_csv(output / "manufacturer-comparison.csv", comparisons)
    return {
        "status": "pass" if all(r["status"] == "pass" for r in comparisons) else "fail",
        "conditions": 18,
        "frequency_points": 684,
        "comparisons": len(comparisons),
        "tolerance_db": 0.0050001,
        "errors": {
            key: summary([r["delta_db"] for r in comparisons if r["metric"] == key])
            for key in ("gain_db", "isolation_db", "input_return_loss_db", "output_return_loss_db")
        },
        "scope": "厂商S参数与同源官方表格的读取/单位/舍入一致性；不是独立器件测量",
    }, all_datasets


def independent_network(cache, output, datasets):
    names = ("PGA-103+_5V_Plus25DegC.s2p", "PGA-103+_3V_Plus25DegC.s2p")
    f = np.linspace(500e6, 4e9, 71)
    frequency = rf.Frequency.from_f(f, unit="hz")
    with ZipFile(cache / "PGA-103+_S2P.zip") as archive:
        nets = []
        for name in names:
            stream = StringIO(archive.read(name).decode())
            stream.name = name
            nets.append(rf.Network(stream).interpolate(frequency))
    delay = np.zeros((len(f), 2, 2), complex)
    delay[:, 0, 1] = delay[:, 1, 0] = 10 ** (-6 / 20) * np.exp(-2j * np.pi * f * 1e-9)
    adapter = rf.Network(frequency=frequency, s=delay, z0=50)
    reference = nets[0] ** adapter ** nets[1]
    ds = {
        "kind": "sparameter",
        "frequency_hz": f.tolist(),
        "s_real": delay.real.tolist(),
        "s_imag": delay.imag.tolist(),
        "z0_ohm": [[50, 50]] * len(f),
        "metadata": {"source": "analytic matched reciprocal 6 dB delay, 1 ns"},
    }
    stages = tuple(
        Stage(id=f"network-{i}", order=i, name=name, gain_db=None, noise=NoiseSpec("unknown"))
        for i, name in enumerate(("PGA-103+ 5V", "6 dB / 1 ns", "PGA-103+ 3V"), 1)
    )
    models = {s.id: {"linear": d} for s, d in zip(stages, (datasets[names[0]], ds, datasets[names[1]]))}
    gs, gl = 0.2 + 0.1j, -0.15 + 0.05j
    p = Project(
        schema_version="2.0.0",
        project_id="public-pga-mismatch-network",
        project_name="厂商网络独立对照",
        link_name="PGA-103+ · 失配级联",
        stages=stages,
        analysis=Analysis(source_frequency_hz=1e9),
        lab={
            "models": models,
            "settings": {
                "source_gamma": [gs.real, gs.imag],
                "load_gamma": [gl.real, gl.imag],
                "sweep_low_hz": float(f[0]),
                "sweep_high_hz": float(f[-1]),
            },
        },
        notes="两份厂商S参数加解析匹配延时网络；scikit-rf作为独立级联求解参照，但Touchstone解析库共用。不代表这条实际硬件链路的测量。无完整噪声参数，NF不验收。",
    )
    rows = []
    for freq, s in zip(f, reference.s):
        ours = evaluate(p, float(freq))[0]
        denominator = (1 - s[0, 0] * gs) * (1 - s[1, 1] * gl) - s[0, 1] * s[1, 0] * gs * gl
        gain = 10 * np.log10((1 - abs(gs) ** 2) * (1 - abs(gl) ** 2) * abs(s[1, 0] / denominator) ** 2)
        expected = {
            "gain_db": float(gain),
            "phase_deg": float(np.angle(s[1, 0] / denominator, deg=True)),
            "s11_db": float(20 * np.log10(abs(s[0, 0]))),
            "s22_db": float(20 * np.log10(abs(s[1, 1]))),
            "s21_real": float(s[1, 0].real),
            "s21_imag": float(s[1, 0].imag),
        }
        for key, value in expected.items():
            delta = ours[key] - value
            if key == "phase_deg":
                delta = (delta + 180) % 360 - 180
            rows.append(
                {
                    "frequency_hz": float(freq),
                    "metric": key,
                    "actual": ours[key],
                    "reference": value,
                    "delta": delta,
                    "tolerance": 1e-9,
                    "status": "pass" if abs(delta) < 1e-9 else "fail",
                }
            )
    atomic_write(output / "projects/PGA-103+-失配级联.json", dumps(p).encode("utf-8"))
    save_csv(output / "network-comparison.csv", rows)
    return {
        "status": "pass" if all(r["status"] == "pass" for r in rows) else "fail",
        "reference": f"scikit-rf {rf.__version__} cascade plus terminated transducer-gain equation",
        "frequencies": len(f),
        "comparisons": len(rows),
        "errors": {k: summary([r["delta"] for r in rows if r["metric"] == k]) for k in expected},
        "nf_status": "not_evaluated_missing_noise_parameters",
    }


def published(output):
    results = {}
    for name, p in published_examples().items():
        result = compare(p, p.lab["reference"])
        if result["status"] != "published_match":
            raise AssertionError(result)
        atomic_write(output / "projects" / f"{name}.json", dumps(p).encode("utf-8"))
        atomic_write(output / f"{name}-参考.csv", csv_text(p.lab["reference"]["rows"]).encode("utf-8"))
        atomic_write(output / f"{name}-对标.zip", handoff(p))
        save_json(output / f"{name}-对比.json", result)
        results[name] = {
            "status": result["status"],
            "rows": len(result["rows"]),
            "error_summary": result["error_summary"],
            "url": result["metadata"]["url"],
            "scope": result["metadata"]["scope"],
        }
    return results


def iq_validation(cache, output):
    folder = cache / "opendpd"
    arrays, datasets = {}, {}
    spec = json.loads((folder / "spec.json").read_text("utf-8"))
    fs = float(spec["input_signal_fs"])
    for split in ("train", "val", "test"):
        x, y = [
            np.loadtxt(folder / f"{split}_{kind}.csv", delimiter=",", skiprows=1).view(np.complex128).ravel()
            for kind in ("input", "output")
        ]
        digest = hashlib.sha256(
            (folder / f"{split}_input.csv").read_bytes() + (folder / f"{split}_output.csv").read_bytes()
        ).hexdigest()
        arrays[split] = (x, y)
        datasets[split] = {
            "kind": "iq",
            "i_in": x.real.tolist(),
            "q_in": x.imag.tolist(),
            "i_out": y.real.tolist(),
            "q_out": y.imag.tolist(),
            "metadata": {
                "sha256": digest,
                "source": "OpenDPD DPA_200MHz measured dataset",
                "amplitude_unit": "normalized",
            },
        }
    candidates, models = [], []
    warmup = 7
    vx, vy = arrays["val"]
    for order in (1, 3, 5, 7, 9):
        for depth in (1, 3, 5, 8):
            model = fit_memory(
                datasets["train"], fs, order, depth, ridge=1e-8, validation_data=datasets["val"]
            )
            validation = evm(vy[warmup:], apply_model(vx, model, fs)[warmup:])
            candidates.append(
                {
                    "order": order,
                    "depth": depth,
                    "validation_nmse_db": validation["nmse_db"],
                    "validation_samples": len(vx) - warmup,
                }
            )
            models.append(model)
    best = min(range(len(candidates)), key=lambda i: candidates[i]["validation_nmse_db"])
    model = models[best]
    tx, ty = arrays["test"]
    prediction = apply_model(tx, model, fs)
    test = evm(ty[warmup:], prediction[warmup:])
    baseline = evm(ty[warmup:], apply_model(tx, models[0], fs)[warmup:])
    measured = wave_metrics(tx[warmup:], ty[warmup:], fs, 200e6, 200e6)["metrics"]
    predicted = wave_metrics(tx[warmup:], prediction[warmup:], fs, 200e6, 200e6)["metrics"]
    report = {
        "status": "evaluated_no_product_acceptance_threshold",
        "dataset": "OpenDPD DPA_200MHz",
        "commit": "aba888b87199d7ae7802ce931987e3aa3c951410",
        "amplitude_unit": "normalized",
        "absolute_power_status": "not_calibrated_not_reported",
        "sample_rate_hz": fs,
        "split_samples": {k: len(v[0]) for k, v in arrays.items()},
        "selection": "20 fixed candidates selected using validation split only; test outputs never used for training or candidate selection",
        "warmup_samples": warmup,
        "selected": candidates[best],
        "candidates": candidates,
        "test": test,
        "linear_baseline_test": baseline,
        "nmse_improvement_db": baseline["nmse_db"] - test["nmse_db"],
        "adjacent_channel": {
            k: {"measured": measured[k], "predicted": predicted[k], "error_db": predicted[k] - measured[k]}
            for k in ("acpr_left_dbc", "acpr_right_dbc")
        },
        "metric_scope": "time-sample prediction EVM/NMSE; Hann-window 200 MHz bands at offsets +/-200 MHz; not standards-demodulated EVM or an OpenDPD benchmark reproduction",
    }
    save_json(output / "opendpd-measured-iq.json", report)
    save_json(output / "opendpd-normalized-memory-model.json", model)
    save_csv(output / "opendpd-candidates.csv", candidates)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=ROOT / "artifacts/public-validation")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/public-validation")
    args = parser.parse_args()
    sources = fetch_sources(args.cache)
    manufacturer_result, datasets = manufacturer(args.cache, args.output)
    network_result = independent_network(args.cache, args.output, datasets)
    report = {
        "date": "2026-09-23",
        "manufacturer": manufacturer_result,
        "network": network_result,
        "published": published(args.output),
        "measured_iq": iq_validation(args.cache, args.output),
        "commercial_solver_executed": False,
        "independent_target_hardware_measured": False,
        "sources": sources,
    }
    save_json(args.output / "report.json", report)
    save_json(ROOT / "docs/公开数据验证记录.json", report)
    if manufacturer_result["status"] != "pass" or network_result["status"] != "pass":
        raise AssertionError("公开数据核对失败，见报告")
    print(
        json.dumps(
            {
                "manufacturer_comparisons": manufacturer_result["comparisons"],
                "network_comparisons": network_result["comparisons"],
                "published_rows": sum(r["rows"] for r in report["published"].values()),
                "test_nmse_db": report["measured_iq"]["test"]["nmse_db"],
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
