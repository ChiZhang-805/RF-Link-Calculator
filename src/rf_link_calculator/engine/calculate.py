"""Pure scalar engine. No UI, file generation or Office dependencies."""

import math
from datetime import datetime, timezone

from rf_link_calculator import FORMULA_VERSION, MODEL_VERSION
from rf_link_calculator.domain.codec import hashes, project_from_dict
from rf_link_calculator.domain.labels import METRICS
from rf_link_calculator.domain.models import Issue, Metric, Project, ResultBundle, StageResult
from rf_link_calculator.domain.validation import validate

from .compression import compression_loss, input_reference, solve_system_p1db
from .frequency import map_frequency
from .noise import added_noise
from .units import KB, LN10, T0, finite_exp, logadd

ASSUMPTIONS = (
    "全链路50 Ω匹配、窄带平坦标量参数；不含S参数失配和任意分支",
    "NF参考温度固定290 K；源温度与器件温度分开",
    "IP3采用相干最坏相位弱非线性预算，与压缩模型独立",
    "P1锚定软饱和为单音CW模型估算；双音总功率仅作粗略筛查",
    "图形导出为单向输出，编辑图中文字不会改变计算输入",
)


def metric(value, unit="dB", status="valid", **kwargs):
    if value is not None and not math.isfinite(value):
        return Metric(None, unit, "failed", reason="超出双精度数值域")
    return Metric(
        value, unit, status if value is not None else (status if status != "valid" else "unknown"), **kwargs
    )


def calculate(project: Project) -> ResultBundle:
    """Validate a detached input snapshot and compute independently gated metrics."""
    project = project_from_dict(project.to_dict())
    if project.lab and (
        project.lab.get("models")
        or project.lab.get("settings", {}).get("source_gamma")
        or project.lab.get("settings", {}).get("load_gamma")
    ):
        from rf_link_calculator.advanced.service import calculate as advanced_calculate

        return advanced_calculate(project)
    issues = list(validate(project))
    a = project.analysis
    g, noise_log, inv_ip3_log = 0.0, -math.inf, -math.inf
    noise_ok, ip3_ok = True, True
    x, f = a.input_power_dbm, a.source_frequency_hz
    rows = []
    t = a.two_tone
    tone_f = [f - t.spacing_hz / 2, f + t.spacing_hz / 2, f - 1.5 * t.spacing_hz, f + 1.5 * t.spacing_hz]
    tone_valid = min(tone_f) > 0
    for s in project.stages:
        m = {}
        before = g
        fin = f
        enabled = s.enabled
        if enabled:
            if f is not None:
                if s.frequency_range_hz:
                    low, high = s.frequency_range_hz
                    if not low <= f - a.signal_bandwidth_hz / 2 or not f + a.signal_bandwidth_hz / 2 <= high:
                        issues.append(
                            Issue(
                                "W004",
                                "warning",
                                "frequency_range_hz",
                                f"实际通带中心{f:g} Hz超出声明范围或带宽边缘",
                                s.id,
                            )
                        )
                    if any(v < low or v > high for v in tone_f):
                        tone_valid = False
                f, inverted = map_frequency(s, f)
                if s.type == "mixer":
                    if s.mixer is None or f is None or f <= a.signal_bandwidth_hz / 2:
                        issues.append(Issue("E003", "error", "mixer", "变频输出跨零频或LO参数不完整", s.id))
                        f = None
                        noise_ok = False
                        tone_valid = False
                    else:
                        lo = s.mixer.lo_frequency_hz
                        if s.mixer.relation == "difference" and min(tone_f) <= lo <= max(tone_f):
                            tone_valid = False
                        tone_f = [map_frequency(s, v)[0] for v in tone_f]
                        m["spectrum_inverted"] = metric(float(inverted), "bool")
            g = None if g is None or s.gain_db is None else g + s.gain_db
        m["frequency_input_hz"] = metric(fin, "Hz")
        m["frequency_output_hz"] = metric(f, "Hz")
        m["stage_gain_db"] = metric(s.gain_db if enabled else 0)
        m["gain_db"] = metric(g)
        m["linear_input_dbm"] = metric(None if before is None else a.input_power_dbm + before, "dBm")
        m["linear_output_dbm"] = metric(None if g is None else a.input_power_dbm + g, "dBm")
        excess = added_noise(s)
        m["stage_nf_db"] = metric(None if excess is None else 10 * math.log1p(excess) / LN10)
        contribution = None
        if enabled and (excess is None or before is None):
            noise_ok = False
        elif enabled and excess > 0:
            contribution_log = math.log(excess) - before * LN10 / 10
            noise_log = logadd(noise_log, contribution_log)
            contribution = finite_exp(contribution_log + math.log(T0))
        else:
            contribution = 0.0
        m["noise_contribution_k"] = metric(contribution, "K")
        m["nf_db"] = metric(10 * logadd(0.0, noise_log) / LN10 if noise_ok else None)
        te = (0.0 if noise_log == -math.inf else finite_exp(noise_log + math.log(T0))) if noise_ok else None
        m["te_k"] = metric(te, "K", "failed" if noise_ok and te is None else "valid")
        nin = None
        if noise_ok:
            total_t_log = logadd(math.log(a.source_noise_temperature_k), noise_log + math.log(T0))
            nin = 10 / LN10 * (math.log(KB) + math.log(a.noise_bandwidth_hz) + total_t_log) + 30
        nout = nin + g if nin is not None and g is not None else None
        m["noise_input_dbm"] = metric(nin, "dBm")
        m["noise_output_dbm"] = metric(nout, "dBm", reference="chain_output")
        m["snr_db"] = metric(a.input_power_dbm - nin if nin is not None else None)
        iip3 = input_reference(s, "ip3")
        m["stage_iip3_dbm"] = metric(iip3, "dBm", "ideal" if s.ip3.mode == "ideal" else "valid")
        m["stage_oip3_dbm"] = metric(
            iip3 + s.gain_db if iip3 is not None else None,
            "dBm",
            "ideal" if s.ip3.mode == "ideal" else "valid",
        )
        if enabled:
            if s.ip3.mode == "unknown" or (s.ip3.mode == "finite" and iip3 is None) or before is None:
                ip3_ok = False
            elif s.ip3.mode == "finite":
                inv_ip3_log = logadd(inv_ip3_log, (before - iip3) * LN10 / 10)
        ipstatus = "unknown" if not ip3_ok else ("ideal" if inv_ip3_log == -math.inf else "valid")
        ip = -10 * inv_ip3_log / LN10 if ipstatus == "valid" else None
        m["iip3_dbm"] = metric(ip, "dBm", ipstatus)
        m["oip3_dbm"] = metric(
            ip + g if ip is not None and g is not None else None, "dBm", ipstatus, reference="chain_output"
        )
        xin = x
        ip1 = input_reference(s)
        m["stage_ip1_dbm"] = metric(
            ip1, "dBm", "ideal" if s.p1db.mode == "ideal" else "valid", reference="stage_input"
        )
        c = None
        if x is not None:
            if not enabled:
                c = 0.0
            elif s.gain_db is None or s.p1db.mode == "unknown" or (s.p1db.mode == "finite" and ip1 is None):
                x = None
            else:
                c = (
                    0.0
                    if s.p1db.mode == "ideal"
                    else compression_loss(x, ip1, s.compression_p or a.default_compression_p)
                )
                x = x + s.gain_db - c
        m["compressed_input_dbm"] = metric(xin, "dBm", "estimated" if xin is not None else "unknown")
        m["compressed_output_dbm"] = metric(x, "dBm", "estimated" if x is not None else "unknown")
        m["stage_compression_db"] = metric(c, "dB", "estimated" if c is not None else "unknown")
        total_c = a.input_power_dbm + g - x if g is not None and x is not None else None
        m["compression_db"] = metric(total_c, "dB", "estimated" if total_c is not None else "unknown")
        margin = ip1 - xin if ip1 is not None and xin is not None else None
        m["p1_margin_db"] = metric(margin, "dB", "estimated" if margin is not None else "unknown")
        if enabled and ((margin is not None and margin < 3) or (total_c is not None and total_c > 3)):
            issues.append(Issue("W006", "warning", "p1db", "接近P1或进入深压缩；当前输出仅为模型估算", s.id))
        if enabled and s.absolute_max_input_dbm is not None:
            linear_in = m["linear_input_dbm"].value
            tone_total_in = (
                t.each_tone_power_dbm + 10 * math.log10(2) + before
                if t.enabled and before is not None
                else None
            )
            if (linear_in is not None and linear_in > s.absolute_max_input_dbm) or (
                tone_total_in is not None and tone_total_in > s.absolute_max_input_dbm
            ):
                issues.append(
                    Issue(
                        "E007",
                        "error",
                        "absolute_max_input_dbm",
                        "CW或双音总平均线性输入筛查超过绝对最大额定值；与P1阈值不同",
                        s.id,
                    )
                )
        m["tone_output_dbm"] = metric(
            t.each_tone_power_dbm + g if t.enabled and g is not None else None,
            "dBm",
            "valid" if t.enabled else "not_applicable",
        )
        m["im3_dbm"] = metric(
            3 * t.each_tone_power_dbm + g - 2 * ip
            if t.enabled and ip is not None and g is not None and tone_valid
            else None,
            "dBm",
            "valid" if t.enabled and tone_valid else "not_applicable",
        )
        rows.append(StageResult(s.id, s.name, s.enabled, m))
    if rows:
        m = dict(rows[-1].metrics)
    else:
        nin = 10 * math.log10(KB * a.noise_bandwidth_hz * a.source_noise_temperature_k) + 30
        m = {
            "gain_db": metric(0),
            "linear_output_dbm": metric(a.input_power_dbm, "dBm"),
            "nf_db": metric(0),
            "te_k": metric(0, "K"),
            "noise_input_dbm": metric(nin, "dBm"),
            "noise_output_dbm": metric(nin, "dBm"),
            "snr_db": metric(a.input_power_dbm - nin),
            "iip3_dbm": metric(None, "dBm", "ideal"),
            "oip3_dbm": metric(None, "dBm", "ideal"),
            "compressed_output_dbm": metric(a.input_power_dbm, "dBm", "estimated"),
            "compression_db": metric(0, "dB", "estimated"),
            "frequency_output_hz": metric(f, "Hz"),
        }
    p1 = solve_system_p1db(project.stages, a.default_compression_p)
    m["ip1_dbm"] = p1
    m["op1_dbm"] = metric(
        p1.value + g - 1 if p1.value is not None and g is not None else None,
        "dBm",
        p1.status,
        reference="chain_output",
    )
    nin, ip = m["noise_input_dbm"].value, m["iip3_dbm"].value
    sensitivity = nin + a.required_snr_db + a.implementation_loss_db if nin is not None else None
    m["sensitivity_dbm"] = metric(
        sensitivity, "dBm", dependencies=("noise", "required_snr", "implementation_loss")
    )
    m["compression_dr_db"] = metric(
        p1.value - a.compression_backoff_db - sensitivity
        if p1.value is not None and sensitivity is not None
        else None,
        "dB",
        "estimated" if p1.value is not None and sensitivity is not None else "unknown",
    )
    sfdr = 2 / 3 * (ip - nin) if ip is not None and nin is not None else None
    m["sfdr3_db"] = metric(sfdr, dependencies=("noise", "ip3"))
    m["sfdr3_normalized"] = metric(
        sfdr + 20 / 3 * math.log10(a.noise_bandwidth_hz) if sfdr is not None else None, "dB·Hz^(2/3)"
    )
    limit = (nin + 2 * ip) / 3 if nin is not None and ip is not None else None
    m["tone_limit_dbm"] = metric(limit, "dBm")
    m["tone_span_db"] = metric(limit - sensitivity if limit is not None and sensitivity is not None else None)
    if any(
        m[k].value is not None and m[k].value < 0 for k in ("compression_dr_db", "tone_span_db", "sfdr3_db")
    ):
        issues.append(Issue("W014", "warning", "dynamic_range", "上下限无重叠，保留负动态范围"))
    total = t.each_tone_power_dbm + 10 * math.log10(2)
    m["tone_input_dbm"] = metric(
        t.each_tone_power_dbm if t.enabled else None, "dBm", "valid" if t.enabled else "not_applicable"
    )
    m["tone_total_dbm"] = metric(
        total if t.enabled else None, "dBm", "valid" if t.enabled else "not_applicable"
    )
    tone_status = "valid"
    reason = "同通道平坦标量、弱非线性外推"
    if not t.enabled:
        tone_status, reason = "not_applicable", "未启用独立双音分析"
    elif not tone_valid:
        tone_status, reason = "not_applicable", "基波／IM3跨越零频或超出声明通带，需要频率选择性模型"
    elif p1.value is not None and total >= p1.value - a.compression_backoff_db:
        tone_status, reason = "not_applicable", "双音总平均功率接近单音P1，弱非线性外推不适用"
    elif ip is None:
        tone_status, reason = m["iip3_dbm"].status, "IP3参数未知或模型内无三阶失真"
    elif g is None:
        tone_status, reason = "unknown", "增益不完整"
    if t.enabled and tone_status == "not_applicable":
        issues.append(Issue("W009", "warning", "two_tone", reason))
    if t.enabled and p1.status == "unknown":
        issues.append(Issue("W009", "warning", "two_tone", "压缩参数不完整，无法核实双音弱非线性工作区"))
    m["tone_output_dbm"] = metric(
        t.each_tone_power_dbm + g if t.enabled and g is not None else None,
        "dBm",
        "valid" if t.enabled else "not_applicable",
    )
    m["im3_dbm"] = metric(
        3 * t.each_tone_power_dbm + g - 2 * ip if tone_status == "valid" else None,
        "dBm",
        tone_status,
        reason=reason,
    )
    m["im3_dbc"] = metric(
        -2 * (ip - t.each_tone_power_dbm) if tone_status == "valid" else None,
        "dBc",
        tone_status,
        reason=reason,
    )
    for index, v in enumerate(tone_f):
        m[f"tone_frequency_{index}_hz"] = metric(
            v if t.enabled and tone_valid else None,
            "Hz",
            "valid" if t.enabled and tone_valid else "not_applicable",
        )
    result_metrics = {k: v for k, v in m.items() if k in METRICS or k.startswith("tone_frequency_")}
    project_hash, calc_hash = hashes(project)
    return ResultBundle(
        project_hash,
        calc_hash,
        datetime.now(timezone.utc).isoformat(),
        FORMULA_VERSION,
        MODEL_VERSION,
        result_metrics,
        tuple(rows),
        tuple(issues),
        ASSUMPTIONS + project.assumptions,
    )
