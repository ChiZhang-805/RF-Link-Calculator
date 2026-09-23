"""Capture actual dependency versions, numerical performance and test evidence for a local release."""

import importlib.metadata as md
import json
import platform
import statistics
import time
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from rf_link_calculator.diagram.scene import build_scene
from rf_link_calculator.engine import calculate
from rf_link_calculator.examples import examples
from rf_link_calculator.exporters.excel import build_excel


def closure(roots):
    found = set()
    queue = list(roots)
    while queue:
        name = canonicalize_name(queue.pop())
        if name in found:
            continue
        found.add(name)
        for raw in md.requires(name) or []:
            req = Requirement(raw)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                queue.append(req.name)
    return sorted(found)


def main():
    runtime = closure(["streamlit", "numpy", "pandas", "openpyxl", "graphviz", "jsonschema"])
    development = closure(["pytest", "pytest-cov", "ruff", "build", "setuptools", "wheel", "pywin32"])
    header = "# 实测锁定：Windows 11 x64 / Python 3.13.0 / 2026-09-23\n"
    Path("requirements.txt").write_text(
        header + "\n".join(f"{x}=={md.version(x)}" for x in runtime) + "\n", encoding="utf-8"
    )
    Path("requirements-dev.txt").write_text(
        header
        + "-r requirements.txt\n"
        + "\n".join(
            f"{x}=={md.version(x)}" + ("; sys_platform == 'win32'" if x == "pywin32" else "")
            for x in development
            if x not in runtime
        )
        + "\n",
        encoding="utf-8",
    )
    licenses = [
        "# 第三方依赖与授权说明",
        "",
        "本项目独立实现射频公式与矢量符号，没有复制受非商业授权限制的RFCascade源码或收费stencil。依赖通过PyPI安装，不随源码包捆绑其代码或系统字体。下表来自本次安装的分发元数据；分发打包依赖时仍须保留相应许可证。",
        "",
        "| 依赖 | 实测版本 | 包元数据许可证 |",
        "|---|---|---|",
    ]
    for name in sorted(set(runtime + development)):
        meta = md.metadata(name)
        license = (
            meta.get("License-Expression")
            or meta.get("License")
            or "; ".join(
                x.split(" :: ")[-1] for x in meta.get_all("Classifier", []) if x.startswith("License")
            )
            or "请查对应分发包许可证"
        )
        licenses.append(
            f"| {name} | {md.version(name)} | {license.splitlines()[0][:160].replace('|', '/')} |"
        )
    licenses += [
        "",
        "Graphviz Python包不包含dot程序，本机未安装dot；可选安装程序及其许可证另行管理。",
        "",
        "WPS、Microsoft Excel、Visio属于独立桌面产品，未作为本项目依赖分发；用户须使用自己合法可用的环境。",
    ]
    Path("第三方依赖与授权说明.md").write_text("\n".join(licenses) + "\n", encoding="utf-8")
    p = examples()["200级长链路"]
    timing = []
    for _ in range(25):
        start = time.perf_counter()
        result = calculate(p)
        timing.append((time.perf_counter() - start) * 1000)
    start = time.perf_counter()
    scene = build_scene(p, result)
    layout = time.perf_counter() - start
    start = time.perf_counter()
    raw = build_excel(p, result)
    excel = time.perf_counter() - start
    evidence = {
        "os": platform.platform(),
        "python": platform.python_version(),
        "processor": platform.processor(),
        "stages": 200,
        "runs": 25,
        "first_calculation_ms": timing[0],
        "warm_median_ms": statistics.median(timing[1:]),
        "p95_calculation_ms": sorted(timing)[23],
        "layout_seconds": layout,
        "layout_backend": scene.metadata["layout"],
        "excel_export_seconds": excel,
        "xlsx_bytes": len(raw),
        "dependency_versions": {x: md.version(x) for x in runtime},
    }
    Path("docs/性能与环境.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in evidence.items() if k != "dependency_versions"}, ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
