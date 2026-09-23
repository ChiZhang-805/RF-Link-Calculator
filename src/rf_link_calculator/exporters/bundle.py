import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from rf_link_calculator.diagram.scene import build_scene, segmented_scenes
from rf_link_calculator.domain.models import Project, ResultBundle
from rf_link_calculator.persistence.json_io import atomic_write, dumps, hashes, project_from_dict

from .drawio import render_drawio
from .excel import build_excel
from .svg import render_svg

VISIO_NOTE = """本次基础图形格式：SVG与原生draw.io，实际成功文件请查看导出清单。
原生VSDX未启用：当前没有通过目标Windows Visio的原生图形、文本、LO端口和连接器粘接验收。
目标Visio版本：未验证。推荐在draw.io/diagrams.net中打开.drawio，逐器件移动和编辑文字。
SVG保留矢量路径与中文文字，但插入目标Visio后的行为须按该版本验证，不保证成为Visio原生端口与连接器。
.drawio不是Visio原生文件，不能通过改后缀使用；不依赖draw.io导出VSDX。
图形是单向导出，改图中文字不会回写计算模型。重新生成会得到新的图形文件。
"""


def safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:80] or "射频项目"
    if name.split(".")[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *[f"COM{i}" for i in range(1, 10)],
        *[f"LPT{i}" for i in range(1, 10)],
    }:
        name = "_" + name
    return name


def export_bundle(project: Project, result: ResultBundle, strict: bool = False) -> tuple[bytes, dict]:
    project = project_from_dict(project.to_dict())
    result = deepcopy(result)
    if hashes(project)[0] != result.project_hash:
        raise ValueError("W012 输入已修改，必须重新计算后导出")
    if strict and any(i.severity == "error" or i.code in ("W004", "W010", "W015") for i in result.issues):
        raise ValueError("严格报告模式：请先解决错误、频率适用性、噪声口径与来源条件告警")
    manifest = {
        "project_hash": result.project_hash,
        "calculation_hash": result.calculation_hash,
        "schema_version": project.schema_version,
        "formula_version": result.formula_version,
        "compression_model_version": result.compression_model_version,
        "created_at": result.created_at,
        "excel_desktop_recalculation": "未执行",
        "vsdx": {"status": "disabled", "reason": "目标Visio编辑性未验证"},
        "files": [],
    }
    files = {}

    def add(name, builder, capability):
        try:
            value = builder()
            raw = value.encode("utf-8") if isinstance(value, str) else value
            if not raw:
                raise ValueError("生成内容为空")
            files[name] = raw
            manifest["files"].append(
                {
                    "name": name,
                    "status": "success",
                    "bytes": len(raw),
                    "sha256": sha256(raw).hexdigest(),
                    "capability": capability,
                }
            )
        except Exception as exc:
            # Boundary per output format: report the actual failure, never emit fake success files.
            manifest["files"].append(
                {"name": name, "status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
            )

    scene = build_scene(project, result)
    manifest["layout"] = scene.metadata["layout"]
    manifest["layout_warnings"] = scene.warnings
    add("链路输入.json", lambda: dumps(project), "版本化项目回读")
    add(
        "计算结果.json",
        lambda: json.dumps(result.to_dict(), ensure_ascii=False, indent=2, allow_nan=False),
        "逐项状态与静态数值",
    )
    advanced = bool(project.lab and (project.lab.get("models") or project.lab.get("settings")))
    if advanced:
        from rf_link_calculator.advanced.export import snapshot_workbook

    add(
        "链路计算.xlsx",
        lambda: snapshot_workbook(project, result) if advanced else build_excel(project, result),
        "高级模型静态快照，修改工作簿不会重新计算"
        if advanced
        else "真实公式、200级输入、历史快照；未做Excel桌面重算",
    )
    if project.lab:
        from rf_link_calculator.advanced.benchmark import handoff

        add(
            "模型与数据.json",
            lambda: json.dumps(project.lab, ensure_ascii=False, indent=2, allow_nan=False),
            "嵌入器件曲线、IQ、模型参数与来源散列",
        )
        add("外部对标.zip", lambda: handoff(project), "待外部软件执行，不代表已验证")
    add("链路框图.svg", lambda: render_svg(scene), "原生矢量与中文文字")
    pages = segmented_scenes(project, result) if len(project.stages) > 10 else [scene]
    add(
        "链路框图.drawio",
        lambda: render_drawio(pages),
        "原生分组、符号、文字、端口与关联连接器；超过10级分段分页",
    )
    if len(pages) > 1:
        for i, page in enumerate(pages, 1):
            add(f"分段框图/第{i:02d}段.svg", lambda p=page: render_svg(p), "分段矢量；段号标注跨段连接")
    add("Visio导入说明.txt", lambda: VISIO_NOTE, "兼容性边界与未验证项")
    add(
        "计算假设与告警.txt",
        lambda: "\n".join(
            [
                "计算散列: " + result.calculation_hash,
                *result.assumptions,
                *scene.warnings,
                *[
                    f"{i.code} [{i.severity}] {i.stage_id or '项目'} {i.field_path}: {i.message}"
                    for i in result.issues
                ],
            ]
        ),
        "完整假设与告警",
    )
    manifest["status"] = (
        "success" if all(f["status"] == "success" for f in manifest["files"]) else "partial_failure"
    )
    files["导出清单.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as z:
        for name, raw in files.items():
            z.writestr(name, raw)
    return out.getvalue(), manifest


def save_bundle(raw: bytes, directory: Path, project: Project) -> Path:
    directory = directory.expanduser().resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / (safe_name(project.project_name + "_" + project.link_name) + "_" + stamp + ".zip")
    if path.exists():
        raise FileExistsError(path)
    atomic_write(path, raw)
    return path
