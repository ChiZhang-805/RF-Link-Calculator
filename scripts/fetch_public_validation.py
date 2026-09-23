"""Fetch explicitly pinned public measurement files and extract numeric PDF tables."""

import argparse
import hashlib
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fetch_sources(target):
    sources = json.loads((ROOT / "docs/public-validation-sources.json").read_text("utf-8"))
    for source in sources:
        path = target / source["file"]
        if path.exists():
            raw = path.read_bytes()
        else:
            with urllib.request.urlopen(source["url"], timeout=60) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
        if len(raw) != source["bytes"] or hashlib.sha256(raw).hexdigest() != source["sha256"]:
            raise ValueError(f"公开数据散列发生变化，请独立核对：{source['url']}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(raw)
    return sources


def extract_tables(path):
    from pypdf import PdfReader

    pages = []
    for i, page in enumerate(PdfReader(path).pages):
        text = page.extract_text()
        conditions = re.search(
            r"Vd\s*=\s*([\d.]+)V, Id\s*=\s*([\d.]+)mA @ Temperature\s*=\s*([-\d.]+)degC", text
        )
        rows = []
        for line in text.splitlines():
            fields = line.split()
            if len(fields) == 10:
                try:
                    rows.append([float(x) for x in fields])
                except ValueError:
                    continue
        if not conditions or len(rows) != 38:
            raise ValueError(f"厂商表格结构改变：第{i + 1}页")
        pages.append(
            {
                "page": i + 1,
                "voltage_v": float(conditions[1]),
                "current_ma": float(conditions[2]),
                "temperature_c": float(conditions[3]),
                "rows": rows,
            }
        )
    if len(pages) != 18:
        raise ValueError("厂商数据页数改变")
    return pages


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=ROOT / "artifacts/public-validation")
    args = parser.parse_args()
    sources = fetch_sources(args.cache)
    tables = extract_tables(args.cache / "PGA-103+_VIEW.pdf")
    (args.cache / "pga-tables.json").write_text(json.dumps(tables, ensure_ascii=False, indent=2), "utf-8")
    print(
        json.dumps(
            {"verified_files": len(sources), "tables": len(tables), "cache": str(args.cache)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
