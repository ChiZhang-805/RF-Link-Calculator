import importlib.metadata
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path


def data_directory() -> Path:
    return (
        Path(os.environ.get("RF_LINK_DATA_DIR", str(Path.home() / "RF-Link-Calculator-Data")))
        .expanduser()
        .resolve()
    )


def office_registered(name: str) -> bool:
    if os.name != "nt":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, name + ".Application\\CLSID"):
            return True
    except OSError:
        return False


def diagnose(directory: Path | None = None) -> dict:
    directory = directory or data_directory()
    checks = {
        "Python": platform.python_version(),
        "操作系统": platform.platform(),
        "数据目录": str(directory),
    }
    for pkg in ("streamlit", "numpy", "pandas", "openpyxl", "graphviz", "jsonschema"):
        try:
            checks[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            checks[pkg] = "未安装"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory) as f:
            f.write(b"rf-diagnostic")
        checks["数据目录可写"] = True
    except OSError as exc:
        checks["数据目录可写"] = str(exc)
    dot = shutil.which("dot")
    checks["Graphviz"] = "未安装；使用串行布局"
    if dot:
        try:
            r = subprocess.run(
                [dot, "-Tplain"],
                input='digraph G {a[label="中文测试"];a->b;}',
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=True,
                timeout=5,
            )
            checks["Graphviz"] = "中文布局可调用" if "node" in r.stdout else "返回内容异常"
        except (OSError, subprocess.SubprocessError) as exc:
            checks["Graphviz"] = str(exc)
    checks["Excel COM接口注册（可能由WPS接管）"] = office_registered("Excel")
    checks["Excel桌面重算验证"] = "未执行；需目标Excel实测"
    checks["Visio注册"] = office_registered("Visio")
    checks["原生VSDX"] = "未启用；目标版本编辑性验收未通过"
    return checks
