"""Explicit CSV contract. Nested specifications remain JSON, no arbitrary spreadsheet evaluation."""

import csv
import io
import json
from dataclasses import asdict

from rf_link_calculator.domain.models import Project, Stage

from .json_io import MAX_BYTES, project_from_dict

FIELDS = list(asdict(Stage()).keys())
NESTED = {"noise", "ip3", "p1db", "frequency_range_hz", "mixer", "source"}
NUMBERS = {"gain_db", "physical_temperature_k", "compression_p", "absolute_max_input_dbm"}


def export_csv(project: Project) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=FIELDS)
    writer.writeheader()
    for stage in project.stages:
        row = asdict(stage)
        for k, v in row.items():
            if k in NESTED:
                row[k] = json.dumps(v, ensure_ascii=False, allow_nan=False)
            elif isinstance(v, str) and v.startswith(("=", "+", "-", "@", "'")):
                row[k] = "'" + v
        writer.writerow(row)
    return out.getvalue().encode("utf-8-sig")


def import_csv(raw: bytes, project: Project) -> Project:
    if len(raw) > MAX_BYTES:
        raise ValueError("CSV超过5 MiB限制")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if reader.fieldnames != FIELDS:
        raise ValueError("CSV列不符合规范，请使用应用导出的器件CSV模板")
    stages = []
    for row in reader:
        if None in row:
            raise ValueError("CSV包含超出规范的额外单元格")
        if len(stages) >= 200:
            raise ValueError("CSV超过200级")
        for k, v in row.items():
            if v is None:
                raise ValueError("CSV缺少单元格")
            if k in NESTED:
                row[k] = json.loads(v)
            elif k in NUMBERS:
                row[k] = float(v) if v else None
            elif k == "order":
                row[k] = int(v)
            elif k == "enabled":
                if v not in ("True", "False"):
                    raise ValueError("启用状态必须为True或False")
                row[k] = v == "True"
            elif v.startswith("'"):
                row[k] = v[1:]
        stages.append(row)
    data = project.to_dict()
    data["stages"] = stages
    return project_from_dict(data)
