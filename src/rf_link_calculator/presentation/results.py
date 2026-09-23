import numpy as np
import pandas as pd
import streamlit as st

from rf_link_calculator.diagram.scene import build_scene
from rf_link_calculator.domain.labels import METRICS, STATUS
from rf_link_calculator.engine.compression import propagate
from rf_link_calculator.exporters.svg import render_svg


def display_results(project, result, stale):
    if stale:
        st.warning("W012 当前输入已修改，下方是上次计算结果。请提交并计算；导出已禁用。")
    tabs = st.tabs(["系统汇总", "逐级结果", "趋势与压缩扫描", "器件框图", "假设与告警"])
    with tabs[0]:
        for title, keys in [
            ("线性预算", ("gain_db", "linear_output_dbm", "nf_db", "noise_output_dbm", "snr_db")),
            ("失真与压缩", ("iip3_dbm", "oip3_dbm", "ip1_dbm", "op1_dbm", "compression_db")),
            ("动态范围", ("sensitivity_dbm", "sfdr3_db", "compression_dr_db")),
        ]:
            st.subheader(title)
            for col, key in zip(st.columns(len(keys)), keys):
                m = result.metrics[key]
                label, unit = METRICS[key]
                col.metric(
                    label,
                    f"{m.value:.3f} {unit}" if m.value is not None else "—",
                    help=m.reason or "; ".join(m.assumptions or result.assumptions),
                )
                col.caption(STATUS[m.status])
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "指标": METRICS.get(k, (k, ""))[0],
                        "数值": m.value,
                        "单位": m.unit,
                        "状态": STATUS[m.status],
                        "条件": m.reason,
                    }
                    for k, m in result.metrics.items()
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    with tabs[1]:
        advanced = st.checkbox("显示全部逐级指标与状态")
        keys = [
            "frequency_input_hz",
            "frequency_output_hz",
            "gain_db",
            "linear_input_dbm",
            "linear_output_dbm",
            "nf_db",
            "iip3_dbm",
            "compressed_output_dbm",
            "compression_db",
        ]
        extra = {
            "stage_ip1_dbm": "本级输入P1",
            "frequency_input_hz": "本级输入频率",
            "linear_input_dbm": "本级线性输入",
            "stage_gain_db": "本级增益",
            "stage_nf_db": "本级NF",
            "noise_contribution_k": "输入噪声温度贡献",
            "stage_iip3_dbm": "本级IIP3",
            "stage_oip3_dbm": "本级OIP3",
            "compressed_input_dbm": "本级估算输入",
            "stage_compression_db": "本级压缩估算",
            "p1_margin_db": "本级P1输入裕量",
            "spectrum_inverted": "频谱翻转",
        }
        records = []
        for s in result.stages:
            record = {"器件": s.name, "状态": "启用" if s.enabled else "逻辑旁路"}
            for k in s.metrics if advanced else keys:
                m = s.metrics[k]
                label = METRICS.get(k, (extra.get(k, k), ""))[0] + "（" + m.unit + "）"
                record[label] = m.value
                if advanced:
                    record[label + "状态"] = STATUS[m.status]
            records.append(record)
        st.dataframe(pd.DataFrame(records), hide_index=True, width="stretch")
    with tabs[2]:
        if result.stages:
            level = pd.DataFrame(
                {
                    "器件": [f"{i + 1}.{s.name}" for i, s in enumerate(result.stages)],
                    "线性信号 dBm": [s.metrics["linear_output_dbm"].value for s in result.stages],
                    "输出噪声 dBm": [s.metrics["noise_output_dbm"].value for s in result.stages],
                }
            )
            st.line_chart(level, x="器件", y=["线性信号 dBm", "输出噪声 dBm"], y_label="功率（dBm）")
            selection = st.radio("累计指标", ["增益", "NF"], horizontal=True)
            key = "gain_db" if selection == "增益" else "nf_db"
            st.line_chart(
                pd.DataFrame(
                    {
                        "级数": range(1, len(result.stages) + 1),
                        selection: [s.metrics[key].value for s in result.stages],
                    }
                ),
                x="级数",
                y=selection,
                y_label=selection + "（dB）",
            )
        ip1 = result.metrics["ip1_dbm"]
        if ip1.status in ("estimated", "ideal") and result.metrics["gain_db"].value is not None:
            midpoint = ip1.value if ip1.value is not None else project.analysis.input_power_dbm
            c1, c2 = st.columns(2)
            low = c1.number_input(
                "扫描输入下限（dBm）",
                value=min(midpoint - 40, project.analysis.input_power_dbm - 10),
                key="scan-low",
            )
            high = c2.number_input(
                "扫描输入上限（dBm）",
                value=max(midpoint + 10, project.analysis.input_power_dbm + 10),
                key="scan-high",
            )
            if high > low:
                xs = np.linspace(low, high, 201)
                df = pd.DataFrame(
                    {
                        "输入 dBm": xs,
                        "线性输出 dBm": xs + result.metrics["gain_db"].value,
                        "压缩输出（模型估算）dBm": [
                            propagate(project.stages, float(x), project.analysis.default_compression_p)[0]
                            for x in xs
                        ],
                    }
                )
                st.line_chart(
                    df,
                    x="输入 dBm",
                    y=["线性输出 dBm", "压缩输出（模型估算）dBm"],
                    x_label="输入（dBm）",
                    y_label="输出（dBm）",
                )
            else:
                st.error("扫描上限必须大于下限")
        else:
            st.info("压缩参数不完整，输入扫描不可用。")
    with tabs[3]:
        scene = build_scene(project, result)
        st.caption("宽图可缩放；导出包中超过10级会提供分段SVG与多页draw.io。灰度/文字状态同样有效。")
        st.image(render_svg(scene).decode("utf-8"), width="stretch")
        for warning in scene.warnings:
            st.info(warning)
    with tabs[4]:
        for text in result.assumptions:
            st.write("• " + text)
        if result.issues:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "编号": i.code,
                            "级别": i.severity,
                            "器件ID": i.stage_id,
                            "字段": i.field_path,
                            "说明": i.message,
                            "建议": i.suggestion,
                        }
                        for i in result.issues
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        else:
            st.success("没有输入告警；结果仍受上述模型假设约束。")
        st.caption("输入散列：" + result.project_hash + "\n公式版本：" + result.formula_version)
