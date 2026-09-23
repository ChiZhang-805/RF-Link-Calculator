"""Connect advanced models to the existing result contract, with explicit metric gates."""

import math
from dataclasses import replace

from rf_link_calculator.domain.codec import hashes
from rf_link_calculator.domain.models import Issue, Metric

from .network import KB, T0, evaluate
from .nonlinear import cw_chain, p1_chain
from .spectral import require_matched

ASSUMPTIONS = (
    "二端口复数S参数与噪声波协方差级联，实参考阻抗归一化到50 Ω；线性插值，不做频率外推",
    "逐级网络结果为截断前缀网络接指定终端后的预算，不是完整双向网络内部探针；主指标噪声为中心频点窄带预算，带内积分使用频谱页",
    "有源S参数的NF需完整噪声参数；单个标量NF不足以确定失配噪声，缺失时返回未知",
    "功率曲线采用复包络无记忆模型；最低测点以下延拓该点复增益，最高点以上不外推",
    "记忆多项式由前半段IQ训练，后半段隔离验证；结果只针对训练采样率和幅度范围",
    "频谱为前馈复包络FFT；IMT为固定驱动单RF+LO实测表；不等价于通用谐波平衡或全电路求解",
    "谐波页使用显式实射频多项式二端口、频域端口连接和Newton-Krylov残差求解；需另验谐波截断，不含晶体管物理模型或大信号噪声",
    "商业软件与硬件准确度需导入独立参考结果另行验收；合成数据只能验证实现",
)


def metric(value, unit="dB", estimated=False, reason=""):
    return Metric(
        value,
        unit,
        "unknown" if value is None else "estimated" if estimated else "valid",
        model="data_driven_rf_v2",
        reason=reason,
    )


def noise_metrics(values, analysis):
    gain, nf = values["gain_db"], values["nf_db"]
    te = None if nf is None else T0 * (10 ** (nf / 10) - 1)
    nin = (
        None
        if te is None
        else 10 * math.log10(KB * (analysis.source_noise_temperature_k + te) * analysis.noise_bandwidth_hz)
        + 30
    )
    sensitivity = None if nin is None else nin + analysis.required_snr_db + analysis.implementation_loss_db
    return {
        "gain_db": metric(gain),
        "nf_db": metric(nf),
        "te_k": metric(te, "K"),
        "noise_input_dbm": metric(nin, "dBm"),
        "noise_output_dbm": metric(None if nin is None else nin + gain, "dBm"),
        "snr_db": metric(None if nin is None else analysis.input_power_dbm - nin),
        "linear_output_dbm": metric(analysis.input_power_dbm + gain, "dBm"),
        "sensitivity_dbm": metric(sensitivity, "dBm"),
    }


def calculate(project):
    from rf_link_calculator.engine.calculate import calculate as scalar

    values, stages = evaluate(project)
    projected = replace(project, lab=None, schema_version="1.0.0", stages=stages)
    base = scalar(projected)
    metrics = {**base.metrics, **noise_metrics(values, project.analysis)}
    rows = []
    for row, data in zip(base.stages, values["stages"]):
        rows.append(replace(row, metrics={**row.metrics, **noise_metrics(data, project.analysis)}))
    issues = list(base.issues)
    models = project.lab.get("models", {})
    has_nonlinear = any(s.enabled and models.get(s.id, {}).get("nonlinear") for s in project.stages)
    reason = ""
    try:
        require_matched(project)
    except ValueError as exc:
        reason = str(exc)
    if any(
        s.enabled and models.get(s.id, {}).get("linear") and models.get(s.id, {}).get("nonlinear")
        for s in project.stages
    ):
        reason = "同级频率模型和完整功率曲线不能重复叠加增益；请拆成独立级"
    if reason:
        # Do not quietly combine matched IP3/P1 formulas with an arbitrary bilateral network.
        keys = (
            "iip3_dbm",
            "oip3_dbm",
            "ip1_dbm",
            "op1_dbm",
            "compressed_output_dbm",
            "compression_db",
            "compression_dr_db",
            "sfdr3_db",
            "sfdr3_normalized",
            "tone_limit_dbm",
            "tone_span_db",
            "tone_output_dbm",
            "im3_dbm",
            "im3_dbc",
        )
        for k in keys:
            metrics[k] = metric(None, metrics[k].unit, reason=reason)
        for i, row in enumerate(rows):
            m = dict(row.metrics)
            for k in keys + ("compressed_input_dbm", "stage_compression_db", "p1_margin_db"):
                if k in m:
                    m[k] = metric(None, m[k].unit, reason=reason)
            rows[i] = replace(row, metrics=m)
        issues.append(Issue("A002", "warning", "lab.models", reason))
    elif has_nonlinear:
        modeled = replace(project, stages=stages)
        try:
            out, curve = cw_chain(modeled, project.analysis.input_power_dbm)
            metrics["compressed_output_dbm"] = metric(out, "dBm", True)
            metrics["compression_db"] = metric(
                project.analysis.input_power_dbm + values["gain_db"] - out, estimated=True
            )
            for i, (row, point) in enumerate(zip(rows, curve)):
                m = dict(row.metrics)
                m["compressed_input_dbm"] = metric(point["input_dbm"], "dBm", True)
                m["compressed_output_dbm"] = metric(point["output_dbm"], "dBm", True)
                m["stage_compression_db"] = metric(
                    point["input_dbm"]
                    + (stages[i].gain_db if stages[i].enabled else 0)
                    - point["output_dbm"],
                    estimated=True,
                )
                m["compression_db"] = metric(
                    project.analysis.input_power_dbm + values["stages"][i]["gain_db"] - point["output_dbm"],
                    estimated=True,
                )
                rows[i] = replace(row, metrics=m)
        except ValueError as exc:
            metrics["compressed_output_dbm"] = metric(None, "dBm", reason=str(exc))
            metrics["compression_db"] = metric(None, reason=str(exc))
            for i, row in enumerate(rows):
                m = dict(row.metrics)
                for key in (
                    "compressed_input_dbm",
                    "compressed_output_dbm",
                    "stage_compression_db",
                    "compression_db",
                    "p1_margin_db",
                ):
                    if key in m:
                        m[key] = metric(None, m[key].unit, reason=str(exc))
                rows[i] = replace(row, metrics=m)
            issues.append(Issue("A003", "warning", "lab.models.nonlinear", str(exc)))
        p1 = p1_chain(modeled, values["gain_db"])
        metrics["ip1_dbm"] = metric(p1, "dBm", True, "数据范围内未找到交点" if p1 is None else "")
        metrics["op1_dbm"] = metric(None if p1 is None else p1 + values["gain_db"] - 1, "dBm", True)
        # IP3 is a separate specified small-signal model, never inferred uniquely from AM/AM.
        for k in ("im3_dbm", "im3_dbc"):
            metrics[k] = metric(None, metrics[k].unit, reason="使用频谱分析计算独立双音结果")
    nin, ip = metrics["noise_input_dbm"].value, metrics["iip3_dbm"].value
    sfdr = None if nin is None or ip is None else 2 / 3 * (ip - nin)
    metrics["sfdr3_db"] = metric(sfdr)
    metrics["sfdr3_normalized"] = metric(
        None if sfdr is None else sfdr + 20 / 3 * math.log10(project.analysis.noise_bandwidth_hz),
        "dB·Hz^(2/3)",
    )
    limit = None if nin is None or ip is None else (nin + 2 * ip) / 3
    sensitivity = metrics["sensitivity_dbm"].value
    metrics["tone_limit_dbm"] = metric(limit, "dBm")
    metrics["tone_span_db"] = metric(None if limit is None or sensitivity is None else limit - sensitivity)
    p1 = metrics["ip1_dbm"].value
    metrics["compression_dr_db"] = metric(
        None
        if p1 is None or sensitivity is None
        else p1 - project.analysis.compression_backoff_db - sensitivity,
        estimated=True,
    )
    if values["nf_db"] is None:
        issues.append(Issue("A001", "warning", "lab.models.linear.noise", "失配噪声需要完整噪声参数"))
    ph, ch = hashes(project)
    return replace(
        base,
        project_hash=ph,
        calculation_hash=ch,
        formula_version="2.0.0",
        compression_model_version="data_driven_v2",
        metrics=metrics,
        stages=tuple(rows),
        issues=tuple(issues),
        assumptions=ASSUMPTIONS + project.assumptions,
    )
