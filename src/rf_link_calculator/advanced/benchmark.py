"""Reproducible handoff and strict external-result comparison; no self-certification."""

import csv
import io
import json
from zipfile import ZIP_DEFLATED, ZipFile

from rf_link_calculator.domain.codec import hashes
from rf_link_calculator.engine import calculate
from rf_link_calculator.engine.compression import input_reference
from rf_link_calculator.engine.noise import added_noise
from rf_link_calculator.persistence.json_io import dumps

from .data import finite

FIELDS = ("scope", "metric", "value", "unit", "status")
DEFAULT_METRICS = ("gain_db", "nf_db", "iip3_dbm", "linear_output_dbm", "frequency_output_hz")


def rows_for(project, analysis=None):
    if analysis:
        from .api import dispatch

        kind = analysis.get("kind")
        if kind not in (
            "sweep",
            "spectrum",
            "waveform",
            "noise",
            "image",
            "image-auto",
            "power",
            "convergence",
            "harmonic",
            "harmonic-convergence",
            "circuit-dc",
            "circuit-hb",
            "circuit-hb-convergence",
            "circuit-noise",
            "circuit-noise-convergence",
        ):
            raise ValueError("对标分析类型无效")
        result = dispatch(kind, project, analysis.get("parameters", {}))
        rows = []
        fields = {
            "gain_db": "dB",
            "nf_db": "dB",
            "phase_deg": "deg",
            "s11_db": "dB",
            "s22_db": "dB",
            "s21_real": "ratio",
            "s21_imag": "ratio",
            "wave_real": "sqrt_mW",
            "wave_imag": "sqrt_mW",
            "power_dbm": "dBm/Hz" if kind == "noise" else "dBm",
            "pout_dbm": "dBm",
            "compression_db": "dB",
            "voltage_real_v": "V",
            "voltage_imag_v": "V",
            "voltage_noise_v2_hz": "V2/Hz",
            "noise_dbm_hz": "dBm/Hz",
        }
        for point in result.get("points", []):
            scope = (
                ("input:" + format(point["pin_dbm"], ".12g"))
                if kind == "power"
                else "frequency:" + format(point["frequency_hz"], ".12g")
            )
            for key, unit in fields.items():
                if key in point:
                    value = point[key]
                    status = (
                        "ideal"
                        if value is None and key in ("s11_db", "s22_db")
                        else "unknown"
                        if value is None
                        else "valid"
                    )
                    rows.append(
                        {
                            "scope": scope,
                            "metric": key,
                            "value": "" if value is None else format(value, ".15g"),
                            "unit": unit,
                            "status": status,
                        }
                    )
        for row in result.get("nodes", []) + result.get("devices", []):
            for key, value in row.items():
                if key in ("node", "id"):
                    continue
                rows.append(
                    {
                        "scope": "circuit:" + row.get("node", row.get("id", "")),
                        "metric": key,
                        "value": format(value, ".15g"),
                        "unit": "A" if key.endswith("_a") else "V",
                        "status": "valid",
                    }
                )
        for key, value in result.get("metrics", {}).items():
            if isinstance(value, (float, int)):
                unit = (
                    "%"
                    if key.endswith("percent")
                    else "dBm"
                    if key.endswith("dbm")
                    else "dBc"
                    if key.endswith("dbc")
                    else "dB"
                    if key.endswith("db")
                    else "ratio"
                )
                rows.append(
                    {
                        "scope": "waveform",
                        "metric": key,
                        "value": format(value, ".15g"),
                        "unit": unit,
                        "status": "valid",
                    }
                )
        for key in ("integrated_noise_dbm", "noise_output_dbm", "equivalent_noise_temperature_k") + (
            ("nf_db", "gain_db", "image_gain_db", "image_frequency_hz") if kind == "image-auto" else ()
        ):
            if key in result:
                rows.append(
                    {
                        "scope": "chain",
                        "metric": key,
                        "value": format(result[key], ".15g"),
                        "unit": "K"
                        if key.endswith("_k")
                        else "Hz"
                        if key.endswith("_hz")
                        else "dB"
                        if key.endswith("_db")
                        else "dBm",
                        "status": "valid",
                    }
                )
        if not rows:
            raise ValueError("对标分析没有可用结果")
        return rows
    result = calculate(project)
    rows = []
    for scope, metrics in [("chain", result.metrics)] + [
        (s.stage_id, s.metrics) for s in result.stages if s.enabled
    ]:
        for k in DEFAULT_METRICS:
            m = metrics[k]
            rows.append(
                {
                    "scope": scope,
                    "metric": k,
                    "value": "" if m.value is None else format(m.value, ".15g"),
                    "unit": m.unit,
                    "status": m.status,
                }
            )
    return rows


def csv_text(rows):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def compare(project, reference, tolerance_db=0.001):
    tolerance_db = finite(tolerance_db)
    if not 0 < tolerance_db <= 1:
        raise ValueError("对标容差应在0～1 dB之间")
    meta = reference.get("metadata", {})
    if meta.get("calculation_hash") != hashes(project)[1]:
        raise ValueError("参考结果与当前计算输入散列不一致")
    if not all(str(meta.get(k, "")).strip() for k in ("software", "version", "solver", "source")):
        raise ValueError("参考结果需要软件、版本、求解器与来源")
    expected = {(r["scope"], r["metric"]): r for r in rows_for(project, meta.get("analysis"))}
    provided = {}
    for row in reference["rows"]:
        key = (row["scope"], row["metric"])
        if key in provided or key not in expected:
            raise ValueError("参考结果含重复或未知指标")
        provided[key] = row
    rows = []
    for key, ours in expected.items():
        ref = provided.get(key)
        delta = None
        limit = max(1e-6, abs(float(ours["value"] or 0)) * 1e-9) if ours["unit"] == "Hz" else tolerance_db
        if ours["unit"] == "ratio":
            limit = 1e-6
        elif ours["unit"] == "sqrt_mW":
            limit = max(1e-12, abs(float(ours["value"] or 0)) * (10 ** (tolerance_db / 20) - 1))
        elif ours["unit"] in ("V", "A", "V2/Hz"):
            floor = {"V": 1e-12, "A": 1e-15, "V2/Hz": 1e-30}[ours["unit"]]
            exponent = 10 if ours["unit"] == "V2/Hz" else 20
            limit = max(floor, abs(float(ours["value"] or 0)) * (10 ** (tolerance_db / exponent) - 1))
        elif ours["unit"] == "deg":
            limit = 0.01
        status = "missing"
        if ref:
            if ref["unit"] != ours["unit"] or ref["status"] != ours["status"]:
                status = "mismatch"
            elif ours["value"] == "":
                status = "pass" if ref["value"] == "" and ours["status"] == "ideal" else "unavailable"
            elif ref["value"] != "":
                delta = finite(ours["value"]) - finite(ref["value"])
                if ours["unit"] == "deg":
                    delta = (delta + 180) % 360 - 180
                status = "pass" if abs(delta) <= limit else "fail"
        rows.append(
            {
                **ours,
                "reference": None if not ref or ref["value"] == "" else finite(ref["value"]),
                "delta": delta,
                "tolerance": limit,
                "comparison": status,
            }
        )
    all_pass = bool(rows) and all(r["comparison"] == "pass" for r in rows)
    independent = (
        meta.get("source") in ("commercial", "measurement", "independent")
        and meta.get("software") != "RF Link"
    )
    import math

    errors = {}
    for row in rows:
        if row["delta"] is not None:
            errors.setdefault(row["metric"], []).append(row["delta"])
    error_summary = {
        key: {
            "count": len(values),
            "max_abs_error": max(map(abs, values)),
            "rms_error": math.sqrt(sum(v * v for v in values) / len(values)),
        }
        for key, values in errors.items()
    }
    return {
        "rows": rows,
        "status": "published_match"
        if all_pass and meta.get("source") == "published"
        else "pass"
        if all_pass and independent
        else "internal_only"
        if all_pass
        else "incomplete_or_failed",
        "independent_source_declared": independent,
        "error_summary": error_summary,
        "metadata": meta,
        "calculation_hash": hashes(project)[1],
    }


def matlab_script(project):
    # Exact scalar Friis adapter. Advanced models need the generic case/data handoff.
    if (project.lab or {}).get("models"):
        return None
    import math

    stages = [s for s in project.stages if s.enabled]
    if not stages:
        return None
    lines = [
        "% RF Link scalar Friis reference. Requires MATLAB RF Toolbox.",
        "% This file is generated, not executed or certified by RF Link.",
        "clear elements;",
        "rows = cell(0,5);",
    ]
    frequency = project.analysis.source_frequency_hz
    for i, s in enumerate(stages, 1):
        extra = added_noise(s)
        if s.gain_db is None or extra is None or s.ip3.mode == "unknown":
            return None
        oip = "Inf" if s.ip3.mode == "ideal" else repr(input_reference(s, "ip3") + s.gain_db)
        parameters = (
            f"'Name','stage{i}','Gain',{s.gain_db!r},'NF',{10 * math.log10(1 + extra)!r},'OIP3',{oip}"
        )
        if s.type == "mixer":
            m = s.mixer
            if m is None or not m.noise_compatible or m.noise_convention != "SSB":
                return None
            if m.relation == "difference" and frequency <= m.lo_frequency_hz:
                return None  # No undocumented high-side-LO convention in the adapter.
            converter = "Up" if m.relation == "sum" else "Down"
            lines.append(
                f"e{i} = modulator({parameters},'LO',{m.lo_frequency_hz!r},'ConverterType','{converter}','ImageReject',false,'ChannelSelect',false);"
            )
            frequency = frequency + m.lo_frequency_hz if converter == "Up" else frequency - m.lo_frequency_hz
        else:
            lines.append(f"e{i} = amplifier({parameters});")
    a = project.analysis
    lines += [
        "elements = [" + " ".join(f"e{i}" for i in range(1, len(stages) + 1)) + "];",
        f"b = rfbudget(elements,{a.source_frequency_hz!r},{a.input_power_dbm!r},{a.noise_bandwidth_hz!r});",
        "b.Solver = 'Friis';",
        "computeBudget(b);",
        "keys = {'gain_db','nf_db','iip3_dbm','linear_output_dbm','frequency_output_hz'};",
        "units = {'dB','dB','dBm','dBm','Hz'};",
        "values = [b.TransducerGain(:),b.NF(:),b.IIP3(:),b.OutputPower(:),b.OutputFrequency(:)];",
        "ids = "
        + "{"
        + ",".join(
            "native2unicode(uint8([" + " ".join(str(b) for b in s.id.encode("utf-8")) + "]), 'UTF-8')"
            for s in stages
        )
        + "};",
        "for i = 1:size(values,1)",
        "  for k = 1:5",
        "    status = 'valid'; v = sprintf('%.15g',values(i,k));",
        "    if isinf(values(i,k)), status = 'ideal'; v = ''; end",
        "    rows(end+1,:) = {ids{i},keys{k},v,units{k},status};",
        "  end",
        "end",
        "for k = 1:5",
        "  r = rows(end-5+k,:); r{1} = 'chain'; rows(end+1,:) = r;",
        "end",
    ]
    # Use the final stage's fixed indices when appending chain rows.
    lines[-2] = f"  r = rows({len(stages) * 5}-5+k,:); r{{1}} = 'chain'; rows(end+1,:) = r;"
    lines += [
        "T = cell2table(rows,'VariableNames',{'scope','metric','value','unit','status'});",
        "writetable(T,'reference.csv');",
        "meta.software = 'MATLAB RF Toolbox';",
        "meta.version = version; meta.solver = 'Friis'; meta.source = 'commercial';",
        f"meta.calculation_hash = '{hashes(project)[1]}';",
        "fid = fopen('reference-metadata.json','w'); fprintf(fid,'%s',jsonencode(meta)); fclose(fid);",
    ]
    return "\n".join(lines) + "\n"


def handoff(project, analysis=None):
    out = io.BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        archive.writestr("project.json", dumps(project))
        reference_rows = rows_for(project, analysis)
        archive.writestr("local-results.csv", csv_text(reference_rows))
        archive.writestr(
            "reference-template.csv", csv_text([{**r, "value": "", "status": ""} for r in reference_rows])
        )
        archive.writestr(
            "case.json",
            json.dumps(
                {
                    "calculation_hash": hashes(project)[1],
                    "source": "internal_generated_not_external",
                    "analysis": analysis,
                    "tolerance_db": 0.001,
                    "software": "",
                    "version": "",
                    "solver": "",
                    "model_assumptions": calculate(project).assumptions,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        script = None if analysis else matlab_script(project)
        if script:
            archive.writestr("run_reference.m", script)
        if analysis and analysis.get("kind", "").startswith("circuit-"):
            from copy import deepcopy

            from .circuit import spice_netlist

            spec = deepcopy(project.lab["circuit"])
            if analysis["kind"] == "circuit-dc":
                for element in spec["components"]:
                    if element["type"] == "voltage":
                        element["tones"] = []
            archive.writestr(
                "circuit.cir", spice_netlist(spec, analysis.get("parameters", {}).get("fundamental_hz"))
            )
            archive.writestr(
                "circuit-scope.txt",
                "Intrinsic Ebers-Moll NPN with fixed capacitors and explicit RC network.\nThe SPICE netlist reproduces the deterministic circuit only. Ordinary .noise does not validate pumped periodic noise.\nIS/BF/BR are specified at the analysis temperature. No Early effect, high injection, transit-time charge, flicker, self-heating or breakdown.\n",
            )
        archive.writestr(
            "README.txt",
            "local-results.csv仅为本工具计算，不能作为独立参考。\n在指定商用软件复现project.json的参数、端口、噪声温度、求解器和非线性模型后，填写reference-template.csv。\n基础标量链路提供run_reference.m时可在MATLAB RF Toolbox运行；脚本尚未在本机商业环境执行。\n高级模型需在目标软件重建同一数据模型，不能用Friis结果验收谐波平衡。\n导入时填写case.json中的calculation_hash与外部软件版本、求解器、来源。对比通过仅适用于该输入与指标。\n",
        )
    return out.getvalue()
