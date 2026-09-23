"""Package only source, documentation and explicitly generated samples, with SHA-256 evidence."""

import json
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from rf_link_calculator import __version__
from rf_link_calculator.persistence.json_io import atomic_write

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ("src", "schemas", "scripts", "tests", "docs", "examples", "samples", ".streamlit")
FILES = (
    "app.py",
    "workbench.py",
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-validation.txt",
    ".gitignore",
    "第三方依赖与授权说明.md",
)


def main():
    for name in ("独立安装验证.json", "桌面重算验证.json"):
        report = json.loads((ROOT / "docs" / name).read_text("utf-8"))
        if report["status"] != "passed":
            raise RuntimeError(f"未通过验证：{name}")
        if name == "独立安装验证.json" and report.get("version") != __version__:
            raise RuntimeError("安装验证不是当前应用版本")
    paths = [ROOT / name for name in FILES]
    for folder in FOLDERS:
        paths.extend(
            p
            for p in (ROOT / folder).rglob("*")
            if p.is_file()
            and not any(x == "__pycache__" or x.endswith(".egg-info") for x in p.parts)
            and p.suffix not in (".pyc", ".log")
        )
    wheel = ROOT / f"dist/rf_link_calculator-{__version__}-py3-none-any.whl"
    paths.append(wheel)
    manifest = {
        "version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "V2 data-driven RF models; external commercial/hardware accuracy, Microsoft Excel and Visio acceptance pending",
        "files": [],
    }
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            relative = path.relative_to(ROOT).as_posix()
            raw = path.read_bytes()
            archive.writestr("RF-Link-Calculator/" + relative, raw)
            manifest["files"].append({"name": relative, "bytes": len(raw), "sha256": sha256(raw).hexdigest()})
        archive.writestr(
            "RF-Link-Calculator/发布文件清单.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
    raw = out.getvalue()
    with ZipFile(BytesIO(raw)) as archive:
        assert archive.testzip() is None
    target = ROOT / f"dist/RF-Link-Calculator-{__version__}-source.zip"
    atomic_write(target, raw)
    atomic_write(
        target.with_suffix(".zip.sha256"), (sha256(raw).hexdigest() + "  " + target.name + "\n").encode()
    )
    print(
        json.dumps(
            {"path": str(target), "bytes": len(raw), "files": len(paths), "sha256": sha256(raw).hexdigest()},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
