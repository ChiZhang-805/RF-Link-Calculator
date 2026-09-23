"""Retain installed distribution licenses in the desktop bundle."""

import importlib.metadata
import json
import sys
from pathlib import Path

target = Path(__file__).resolve().parents[1] / "build" / "THIRD-PARTY-NOTICES"
target.mkdir(parents=True, exist_ok=True)
for name in ("LICENSE.txt", "LICENSE_PYTHON.txt", "LICENSE"):
    source = Path(sys.base_prefix) / name
    if source.is_file():
        (target / ("Python-" + name)).write_bytes(source.read_bytes())
manifest = []
for dist in importlib.metadata.distributions():
    name = dist.metadata.get("Name", "unknown")
    manifest.append(
        {
            "name": name,
            "version": dist.version,
            "license": dist.metadata.get("License-Expression")
            or dist.metadata.get("License", "See included license files"),
        }
    )
    for entry in dist.files or []:
        if any(word in entry.name.lower() for word in ("license", "copying", "notice")):
            source = Path(dist.locate_file(entry))
            if source.is_file():
                dest = target / name / str(entry).replace("..", "_").replace("\\", "/")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(source.read_bytes())
(target / "distributions.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"Collected notices for {len(manifest)} distributions")
