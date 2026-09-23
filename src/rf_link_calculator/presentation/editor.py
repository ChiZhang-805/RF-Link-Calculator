from dataclasses import asdict

import pandas as pd
import streamlit as st

from rf_link_calculator.application.project_service import edit_structure
from rf_link_calculator.domain.labels import IP3REF, LINK_TYPES, NOISE, P1REF, POWER, TYPES
from rf_link_calculator.domain.models import Mixer, Stage


def choice(label, mapping, current, key):
    keys = list(mapping)
    return st.selectbox(label, keys, index=keys.index(current), format_func=lambda x: mapping[x], key=key)


def configuration(d: dict, rev: int):
    a = d["analysis"]
    key = lambda name: f"cfg-{rev}-{name}"
    c1, c2, c3 = st.columns(3)
    with c1:
        d["project_name"] = st.text_input("项目名称", d["project_name"], key=key("project"))
        d["link_name"] = st.text_input("链路名称", d["link_name"], key=key("link"))
        d["link_type"] = choice("链路类型", LINK_TYPES, d["link_type"], key("type"))
        a["input_power_dbm"] = st.number_input(
            "主分析单音 CW 输入功率（dBm）",
            value=float(a["input_power_dbm"]),
            key=key("pin"),
            help="始终为独立CW功率；启用双音不会重解释此数值",
        )
    with c2:
        unit = st.selectbox("工作频率单位", ["MHz", "GHz"], key=key("unit"))
        scale = 1e6 if unit == "MHz" else 1e9
        a["source_frequency_hz"] = (
            st.number_input(
                "工作频率（" + unit + "）",
                min_value=1e-9,
                value=float(a["source_frequency_hz"] / scale),
                format="%.6f",
                key=key("freq-" + unit),
            )
            * scale
        )
        a["signal_bandwidth_hz"] = st.number_input(
            "信号带宽（Hz）", min_value=1e-9, value=float(a["signal_bandwidth_hz"]), key=key("sbw")
        )
        a["noise_bandwidth_hz"] = st.number_input(
            "等效噪声带宽（Hz）",
            min_value=1e-9,
            value=float(a["noise_bandwidth_hz"]),
            key=key("nbw"),
            help="与信号带宽分开。相等代表矩形平坦带宽近似。",
        )
        a["source_noise_temperature_k"] = st.number_input(
            "系统温度：输入源等效噪声温度（K）",
            min_value=1e-9,
            value=float(a["source_noise_temperature_k"]),
            key=key("temp"),
            help="NF参考温度固定290 K；各无源器件物理温度在详细参数中设置。",
        )
    with c3:
        a["required_snr_db"] = st.number_input(
            "所需信噪比（dB）", value=float(a["required_snr_db"]), key=key("snr")
        )
        a["implementation_loss_db"] = st.number_input(
            "实现损失（dB）", min_value=0.0, value=float(a["implementation_loss_db"]), key=key("loss")
        )
        a["compression_backoff_db"] = st.number_input(
            "压缩回退量（dB）", min_value=0.0, value=float(a["compression_backoff_db"]), key=key("backoff")
        )
        a["default_compression_p"] = st.number_input(
            "默认压缩形状 p（模型假设）",
            min_value=1.0,
            max_value=10.0,
            value=float(a["default_compression_p"]),
            key=key("p"),
        )
    with st.expander("独立双音分析与项目备注", expanded=a["two_tone"]["enabled"]):
        t = a["two_tone"]
        t["enabled"] = st.checkbox("启用等功率双音 IM3 分析", value=t["enabled"], key=key("two"))
        left, right = st.columns(2)
        t["each_tone_power_dbm"] = left.number_input(
            "每个音的输入功率（dBm）", value=float(t["each_tone_power_dbm"]), key=key("tonepin")
        )
        t["spacing_hz"] = right.number_input(
            "两音间隔（Hz）", min_value=1e-9, value=float(t["spacing_hz"]), key=key("spacing")
        )
        st.caption(
            f"双音总平均输入 = {t['each_tone_power_dbm'] + 3.01029995664:.3f} dBm；只用其粗略筛查单音P1，不能预测真实双音压缩。"
        )
        d["notes"] = st.text_area("项目备注与数据来源说明", d.get("notes", ""), key=key("notes"))


def stage_editor(d: dict, rev: int) -> dict:
    stages = list(d["stages"])
    st.subheader("器件级联")
    st.caption(
        "从上到下计算；表格排序仅改变查看顺序，真正重排请使用下方上移／下移。所有器件都有稳定ID。禁用代表逻辑旁路。"
    )
    columns = [
        "id",
        "启用",
        "名称",
        "类型",
        "增益 dB",
        "噪声模式",
        "NF dB",
        "IP3模式",
        "IP3参考",
        "IP3 dBm",
        "P1模式",
        "P1参考",
        "P1 dBm",
    ]
    rows = [
        {
            "id": s["id"],
            "启用": s["enabled"],
            "名称": s["name"],
            "类型": TYPES[s["type"]],
            "增益 dB": s["gain_db"],
            "噪声模式": NOISE[s["noise"]["mode"]],
            "NF dB": s["noise"]["value_db"],
            "IP3模式": POWER[s["ip3"]["mode"]],
            "IP3参考": IP3REF[s["ip3"]["reference"]],
            "IP3 dBm": s["ip3"]["value_dbm"],
            "P1模式": POWER[s["p1db"]["mode"]],
            "P1参考": P1REF[s["p1db"]["reference"]],
            "P1 dBm": s["p1db"]["value_dbm"],
        }
        for s in stages
    ]
    df = pd.DataFrame(rows, columns=columns)
    for c in ("增益 dB", "NF dB", "IP3 dBm", "P1 dBm"):
        df[c] = pd.to_numeric(df[c]).astype(float)
    opts = {
        "id": None,
        **{
            name: st.column_config.SelectboxColumn(name, options=list(mapping.values()), required=True)
            for name, mapping in (
                ("类型", TYPES),
                ("噪声模式", NOISE),
                ("IP3模式", POWER),
                ("P1模式", POWER),
                ("IP3参考", IP3REF),
                ("P1参考", P1REF),
            )
        },
    }
    edited = st.data_editor(
        df,
        hide_index=True,
        num_rows="fixed",
        disabled=["id"],
        column_config=opts,
        key=f"stages-{rev}",
        width="stretch",
    )
    reverse = lambda mapping, val: next(k for k, v in mapping.items() if v == val)
    nullable = lambda x: None if pd.isna(x) else float(x)
    by_id = {s["id"]: s for s in stages}
    for row in edited.to_dict("records"):
        s = by_id[row["id"]]
        s.update(
            enabled=bool(row["启用"]),
            name=row["名称"] or "",
            type=reverse(TYPES, row["类型"]),
            gain_db=nullable(row["增益 dB"]),
        )
        s["noise"] = {"mode": reverse(NOISE, row["噪声模式"]), "value_db": nullable(row["NF dB"])}
        for key, prefix, refs in (("ip3", "IP3", IP3REF), ("p1db", "P1", P1REF)):
            s[key] = {
                "mode": reverse(POWER, row[prefix + "模式"]),
                "reference": reverse(refs, row[prefix + "参考"]),
                "value_dbm": nullable(row[prefix + " dBm"]),
            }
    d["stages"] = stages
    selected = None
    if stages:
        selected = st.selectbox(
            "选中器件以编辑详细参数或调整顺序",
            [s["id"] for s in stages],
            format_func=lambda id: f"{by_id[id]['order']}. {by_id[id]['name']}",
            key=f"selected-{rev}",
        )
        s = by_id[selected]
        with st.expander("器件详细参数、频率与来源"):
            k = lambda name: f"detail-{rev}-{selected}-{name}"
            c1, c2, c3 = st.columns(3)
            with c1:
                s["physical_temperature_k"] = st.number_input(
                    "器件物理温度（K）",
                    min_value=1e-9,
                    value=float(s["physical_temperature_k"]),
                    key=k("temp"),
                )
                inherit = st.checkbox("继承项目压缩形状p", value=s["compression_p"] is None, key=k("inherit"))
                s["compression_p"] = (
                    None
                    if inherit
                    else st.number_input(
                        "本级压缩形状p",
                        min_value=1.0,
                        max_value=10.0,
                        value=float(s["compression_p"] or d["analysis"]["default_compression_p"]),
                        key=k("p"),
                    )
                )
                s["absolute_max_input_dbm"] = st.number_input(
                    "绝对最大输入功率（dBm，可留空）",
                    value=s["absolute_max_input_dbm"],
                    key=k("max"),
                    help="额定值用于独立告警，不等于P1压缩点。",
                )
            with c2:
                range_on = st.checkbox(
                    "声明适用频率范围", value=bool(s["frequency_range_hz"]), key=k("range")
                )
                if range_on:
                    lo, hi = s["frequency_range_hz"] or (1e6, 3e9)
                    s["frequency_range_hz"] = [
                        st.number_input("频率下限（Hz）", min_value=1e-9, value=float(lo), key=k("lo")),
                        st.number_input("频率上限（Hz）", min_value=1e-9, value=float(hi), key=k("hi")),
                    ]
                else:
                    s["frequency_range_hz"] = None
                s["notes"] = st.text_area(
                    "器件备注／选定通路和其他端口终端", s.get("notes", ""), key=k("notes")
                )
            with c3:
                source = s["source"]
                for field, label in (
                    ("kind", "来源类型"),
                    ("url", "数据手册路径／网址"),
                    ("version", "数据版本"),
                    ("bias", "偏置条件／VGA档位"),
                    ("note", "来源说明"),
                ):
                    value = st.text_input(label, source.get(field, ""), key=k(field))
                    if value or field in source:
                        source[field] = value
                mismatch = st.checkbox(
                    "参数来自不同测试条件，尚未统一",
                    value=source.get("conditions_mismatch", False),
                    key=k("mismatch"),
                )
                if mismatch or "conditions_mismatch" in source:
                    source["conditions_mismatch"] = mismatch
                for field, label in (
                    ("test_frequency_hz", "来源测试频率（Hz，可留空）"),
                    ("temperature_k", "来源测试温度（K，可留空）"),
                ):
                    value = st.number_input(
                        label,
                        min_value=1e-9,
                        value=float(source[field]) if field in source else None,
                        key=k(field),
                    )
                    if value is not None:
                        source[field] = value
                    else:
                        source.pop(field, None)
                spec = st.selectbox(
                    "规格性质",
                    ["", "typ", "min", "max", "assumed"],
                    index=["", "typ", "min", "max", "assumed"].index(source.get("specification", "")),
                    format_func=lambda x: {
                        "": "未声明",
                        "typ": "典型值",
                        "min": "最小值",
                        "max": "最大值",
                        "assumed": "人工假设",
                    }[x],
                    key=k("specification"),
                )
                if spec or "specification" in source:
                    source["specification"] = spec
            if s["type"] == "mixer":
                mix = s["mixer"] or asdict(Mixer())
                c1, c2, c3 = st.columns(3)
                mix["lo_frequency_hz"] = c1.number_input(
                    "LO频率（Hz）", min_value=1e-9, value=float(mix["lo_frequency_hz"]), key=k("LO")
                )
                mix["lo_power_dbm"] = c1.number_input(
                    "LO驱动功率（dBm，可留空）", value=mix["lo_power_dbm"], key=k("LOpower")
                )
                with c2:
                    mix["relation"] = choice(
                        "目标变频关系",
                        {"difference": "差频 |RF-LO|", "sum": "和频 RF+LO"},
                        mix["relation"],
                        k("relation"),
                    )
                    mix["noise_convention"] = st.selectbox(
                        "混频器NF口径",
                        ["unknown", "SSB", "DSB"],
                        index=["unknown", "SSB", "DSB"].index(mix["noise_convention"]),
                        format_func=lambda v: "未知" if v == "unknown" else v,
                        key=k("convention"),
                    )
                mix["noise_compatible"] = c3.checkbox(
                    "确认该NF适用于所选边带与噪声预算", value=mix["noise_compatible"], key=k("compatible")
                )
                s["mixer"] = mix
                st.caption("SSB与DSB不会自动加减3 dB；未确认时停止可靠噪声与SFDR输出。频谱翻转按LO位置计算。")
    buttons = st.columns(5)
    actions = [
        ("add", "添加器件"),
        ("copy", "复制器件"),
        ("delete", "删除器件"),
        ("up", "上移"),
        ("down", "下移"),
    ]
    for col, (action, label) in zip(buttons, actions):
        if col.button(label, disabled=(action != "add" and selected is None), key=f"action-{action}"):
            try:
                new = asdict(Stage(order=len(stages) + 1))
                d = edit_structure(d, action, selected, new)
                st.session_state.draft = d
                st.session_state.revision += 1
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    return d
