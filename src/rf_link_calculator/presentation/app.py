from copy import deepcopy
from pathlib import Path

import streamlit as st

from rf_link_calculator.diagnostics import data_directory, diagnose
from rf_link_calculator.engine import calculate
from rf_link_calculator.examples import examples
from rf_link_calculator.exporters.bundle import export_bundle, safe_name, save_bundle
from rf_link_calculator.persistence.csv_io import export_csv, import_csv
from rf_link_calculator.persistence.json_io import dumps, hashes, loads, project_from_dict, save_project

from .editor import configuration, stage_editor
from .results import display_results


def set_project(project):
    st.session_state.draft = project.to_dict()
    st.session_state.revision = st.session_state.get("revision", 0) + 1
    st.session_state.result = None
    st.session_state.export_data = None


def run():
    st.set_page_config(page_title="RF-Link-Calculator · 射频链路", page_icon="📡", layout="wide")
    st.title("射频链路计算与工程导出")
    st.caption("RF-Link-Calculator · 本地中文工具 · 50 Ω匹配标量预算 · 压缩为模型估算")
    if "draft" not in st.session_state:
        p = examples()["接收链路"]
        set_project(p)
        st.session_state.snapshot = p
        st.session_state.result = calculate(p)
    with st.sidebar:
        st.header("项目管理")
        ex = examples()
        selected = st.selectbox("载入构造示例", list(ex), index=1)
        if st.button("载入示例", width="stretch"):
            set_project(ex[selected])
            st.rerun()
        st.caption("示例均为构造值，不代表实测器件。")
        uploaded = st.file_uploader(
            "打开 JSON 项目／规范 CSV 器件表",
            type=["json", "csv"],
            help="最大5 MiB；CSV请使用下方下载的规范模板。",
        )
        if st.button("导入文件", disabled=uploaded is None):
            try:
                p = (
                    loads(uploaded.getvalue())
                    if uploaded.name.lower().endswith(".json")
                    else import_csv(uploaded.getvalue(), project_from_dict(st.session_state.draft))
                )
                set_project(p)
                st.rerun()
            except (ValueError, TypeError, KeyError) as exc:
                st.error("导入失败：" + str(exc))
        directory = st.text_input(
            "项目数据目录", str(data_directory()), help="项目JSON和导出ZIP保存于此；与程序源码分离。"
        )
        if st.button("运行环境诊断"):
            st.session_state.diagnostics = diagnose(Path(directory))
        if "diagnostics" in st.session_state:
            st.json(st.session_state.diagnostics)
        st.info("原生 VSDX 未启用：目标 Visio 编辑性验收尚未完成。SVG、draw.io 与说明文件可用。")
    d = deepcopy(st.session_state.draft)
    with st.expander("项目与分析条件", expanded=True):
        configuration(d, st.session_state.revision)
    d = stage_editor(d, st.session_state.revision)
    st.session_state.draft = d
    project, error = None, None
    try:
        project = project_from_dict(d)
    except (ValueError, TypeError, KeyError) as exc:
        error = str(exc)
        st.error(error)
    cols = st.columns([2, 1, 1])
    if cols[0].button("提交并计算", type="primary", disabled=project is None, width="stretch"):
        try:
            st.session_state.result = calculate(project)
            st.session_state.snapshot = project
            st.session_state.export_data = None
        except (ValueError, ArithmeticError) as exc:
            st.error("计算失败：" + str(exc))
    if project:
        cols[1].download_button(
            "下载项目 JSON",
            dumps(project),
            safe_name(project.link_name) + ".json",
            "application/json",
            width="stretch",
        )
        cols[2].download_button(
            "下载器件 CSV",
            export_csv(project),
            safe_name(project.link_name) + ".csv",
            "text/csv",
            width="stretch",
        )
        if st.sidebar.button("保存当前项目到数据目录"):
            try:
                path = save_project(
                    project,
                    Path(directory) / (safe_name(project.project_name + "_" + project.link_name) + ".json"),
                )
                st.sidebar.success("已保存：" + str(path))
            except (OSError, ValueError) as exc:
                st.sidebar.error("保存失败：" + str(exc))
    result = st.session_state.result
    stale = result is None or project is None or hashes(project)[0] != result.project_hash
    if result:
        display_results(st.session_state.snapshot, result, stale)
    else:
        st.info("请提交并计算以生成结果。")
    st.divider()
    st.subheader("工程交付包")
    st.caption(
        "含JSON、可重算Excel、SVG、原生draw.io、假设告警、Visio说明及校验清单。Excel桌面重算尚需目标软件验证。"
    )
    strict = st.checkbox("严格报告模式：阻止有错误、频率范围或来源条件不一致的报告")
    if st.button("生成完整导出包", disabled=stale):
        try:
            with st.spinner("生成工作簿、框图与导出清单…"):
                raw, manifest = export_bundle(project, result, strict)
            st.session_state.export_data = (raw, manifest, result.project_hash)
        except (OSError, ValueError) as exc:
            st.error("导出失败：" + str(exc))
    export = st.session_state.get("export_data")
    if export and not stale and export[2] == result.project_hash:
        raw, manifest, _ = export
        if manifest["status"] == "success":
            st.success("基础导出文件全部生成。Excel／Visio软件实测状态见清单。")
        else:
            st.error("部分文件生成失败，请查看清单中的原因。")
        c1, c2 = st.columns(2)
        c1.download_button(
            "下载工程 ZIP",
            raw,
            safe_name(project.link_name) + "_工程包.zip",
            "application/zip",
            width="stretch",
        )
        if c2.button("保存工程包到数据目录", width="stretch"):
            try:
                st.success("已保存：" + str(save_bundle(raw, Path(directory), project)))
            except (OSError, ValueError) as exc:
                st.error("保存失败：" + str(exc))
        with st.expander("查看本次导出清单"):
            st.json(manifest)
