"""Strict bounded JSON, version gate, canonical hashes and atomic backups."""

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from rf_link_calculator.domain.codec import hashes as hashes
from rf_link_calculator.domain.codec import project_from_dict as project_from_dict
from rf_link_calculator.domain.models import Project

MAX_BYTES = 32 * 1024 * 1024


def loads(raw: bytes | str) -> Project:
    encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(encoded) > MAX_BYTES:
        raise ValueError("JSON超过32 MiB限制")

    def constant(value):
        raise ValueError(f"JSON不允许{value}")

    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise ValueError(f"JSON字段重复：{k}")
            out[k] = v
        return out

    try:
        return project_from_dict(
            json.loads(encoded.decode("utf-8-sig"), parse_constant=constant, object_pairs_hook=pairs)
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"无法读取JSON：{exc}") from exc


def dumps(project: Project) -> str:
    return json.dumps(project.to_dict(), ensure_ascii=False, indent=2, allow_nan=False)


def atomic_write(path: Path, content: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".rf-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def save_project(project: Project, path: Path, backups: int = 5) -> Path:
    path = path.expanduser().resolve()
    if path.suffix.lower() != ".json":
        raise ValueError("项目文件必须使用.json扩展名")
    # Verify before touching the previous version.
    raw = dumps(project).encode("utf-8")
    loads(raw)
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        shutil.copy2(path, path.with_name(path.name + f".{stamp}.bak"))
    atomic_write(path, raw)
    # Only application-created backup files of this exact project are rotated.
    for old in sorted(path.parent.glob(path.name + ".*.bak"), reverse=True)[backups:]:
        old.unlink()
    return path
