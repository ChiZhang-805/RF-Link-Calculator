"""Workbench API operations. Every analysis is tied to a detached project snapshot."""

from copy import deepcopy
from dataclasses import replace

from rf_link_calculator.domain.codec import hashes, project_from_dict

from . import benchmark, circuit, harmonic, network, nonlinear, spectral
from .data import finite, import_dataset


def dispatch(action, project, data):
    lab = deepcopy(project.lab or {"models": {}, "settings": {}})
    if action == "circuit-import":
        import json

        text = data["text"]
        if not isinstance(text, str) or len(text.encode("utf-8")) > 512 * 1024:
            raise ValueError("电路文件过大")
        lab["circuit"] = json.loads(text)
        updated = project_from_dict(replace(project, schema_version="2.0.0", lab=lab).to_dict())
        return {"project": updated.to_dict()}
    if action == "circuit-template":
        from .circuit_examples import circuits

        return {"circuits": circuits()}
    if action == "import":
        kind = data["kind"]
        ds = import_dataset(kind, data["text"], data.get("filename", ""), data.get("metadata"))
        if kind in ("iq", "reference"):
            lab[kind] = ds
        else:
            sid = data["stage_id"]
            slot = {
                "frequency": "linear",
                "sparameter": "linear",
                "ampm": "nonlinear",
                "imt": "mixer",
                "harmonic": "harmonic",
            }[kind]
            lab.setdefault("models", {}).setdefault(sid, {})[slot] = ds
        updated = project_from_dict(replace(project, schema_version="2.0.0", lab=lab).to_dict())
        return {"project": updated.to_dict(), "dataset": {"kind": kind, "metadata": ds["metadata"]}}
    if action == "fit":
        source = lab.get("iq")
        if not source:
            raise ValueError("请导入IQ数据")
        if data.get("stage_id") not in {s.id for s in project.stages}:
            raise ValueError("请选择目标器件")
        model = nonlinear.fit_memory(
            source,
            finite(data["sample_rate_hz"]),
            int(data.get("order", 5)),
            int(data.get("depth", 3)),
            int(data.get("delay", 0)),
            finite(data.get("ridge", 0)),
            data.get("validation"),
        )
        lab.setdefault("models", {}).setdefault(data["stage_id"], {})["nonlinear"] = model
        updated = project_from_dict(replace(project, schema_version="2.0.0", lab=lab).to_dict())
        return {"project": updated.to_dict(), "fit": model["metadata"]}
    if action.startswith("circuit-"):
        spec = lab.get("circuit")
        if not spec:
            raise ValueError("请导入电路")
        if action == "circuit-dc":
            result = circuit.operating_point(spec)
        elif action == "circuit-spice":
            return (
                circuit.spice_netlist(spec, data.get("fundamental_hz")).encode("utf-8"),
                "text/plain",
                "电路对标.cir",
            )
        elif action in ("circuit-hb", "circuit-hb-convergence"):
            solve = (
                circuit.harmonic_convergence if action.endswith("convergence") else circuit.harmonic_balance
            )
            result = solve(spec, data["fundamental_hz"], data.get("harmonics", 8))
        elif action in ("circuit-noise", "circuit-noise-convergence"):
            solve = circuit.noise_convergence if action.endswith("convergence") else circuit.periodic_noise
            result = solve(
                spec,
                data["fundamental_hz"],
                data.get("harmonics", 8),
                data.get("offset_hz", 1e4),
                data.get("sidebands", 8),
                data.get("output_harmonics", 1),
            )
        else:
            raise ValueError("未知电路操作")
    elif action in ("harmonic", "harmonic-convergence"):
        solve = harmonic.convergence if action.endswith("convergence") else harmonic.solve
        result = solve(project, finite(data["fundamental_hz"]), data.get("harmonics", 64), data.get("tones"))
    elif action == "sweep":
        result = network.sweep(
            project, finite(data["low"]), finite(data["high"]), int(data.get("count", 201))
        )
    elif action == "spectrum":
        if data.get("mode") == "imt":
            result = spectral.spur_table(project)
        else:
            result = spectral.tones(
                project,
                int(data.get("count", 4096)),
                data.get("scalar_model", "ip3"),
                data.get("tones"),
                int(data.get("oversampling", 1)),
            )
    elif action == "convergence":
        result = spectral.convergence(
            project,
            int(data.get("count", 4096)),
            data.get("scalar_model", "ip3"),
            data.get("tones"),
            int(data.get("oversampling", 1)),
        )
    elif action == "power":
        import numpy as np

        spectral.require_matched(project)
        low, high, count = finite(data["low"]), finite(data["high"]), int(data.get("count", 161))
        if not -300 <= low < high <= 100 or not 2 <= count <= 801:
            raise ValueError("功率扫描范围无效")
        values, stages = network.evaluate(project)
        model_project = replace(project, stages=stages)
        points = []
        for pin in np.linspace(low, high, count):
            try:
                pout = nonlinear.cw_chain(model_project, float(pin))[0]
            except ValueError:
                pout = None
            points.append(
                {
                    "pin_dbm": float(pin),
                    "pout_dbm": pout,
                    "compression_db": None if pout is None else float(pin + values["gain_db"] - pout),
                }
            )
        result = {"points": points, "model": "data_driven_cw_power_scan_v2"}
    elif action == "waveform":
        result = spectral.waveform(
            project,
            finite(data["sample_rate_hz"]),
            finite(data["bandwidth_hz"]),
            finite(data["spacing_hz"]),
            data.get("mode", "predict"),
            data.get("scalar_model", "ip3"),
        )
    elif action == "image":
        result = spectral.image_noise(project, data["image_gain_db"], data["image_temperature_k"])
    elif action == "image-auto":
        result = spectral.automatic_image_noise(project, finite(data.get("image_temperature_k", 290)))
    elif action == "noise":
        result = network.integrated_noise(
            project, finite(data["low"]), finite(data["high"]), int(data.get("count", 401))
        )
    elif action == "compare":
        if not lab.get("reference"):
            raise ValueError("请导入外部参考结果")
        result = benchmark.compare(project, lab["reference"], finite(data.get("tolerance_db", 0.001)))
    elif action == "handoff":
        return benchmark.handoff(project, data.get("analysis")), "application/zip", "对标工程包.zip"
    elif action == "template":
        templates = {
            "frequency": "frequency_hz,gain_db,nf_db,phase_deg,iip3_dbm,ip1_dbm\n90000000,20,1.5,0,5,-9\n110000000,19,1.6,-10,4,-10\n",
            "ampm": "pin_dbm,pout_dbm,phase_deg\n-80,-60,0\n-40,-20,0\n-20,0,0\n-10,9,2\n0,14,8\n",
            "iq": "i_in,q_in,i_out,q_out\n",
            "imt": "m,n,output_dbm\n1,-1,-30\n2,-1,-70\n1,-2,-80\n",
            "harmonic": "order,coefficient\n1,10\n2,0.2\n3,-0.666666666666667\n",
        }
        if data.get("kind") not in templates:
            raise ValueError("该数据类型使用原始器件文件")
        return templates[data["kind"]].encode("utf-8-sig"), "text/csv", data["kind"] + "_template.csv"
    else:
        raise ValueError("未知分析操作")
    return {**result, "calculation_hash": hashes(project)[1]}
