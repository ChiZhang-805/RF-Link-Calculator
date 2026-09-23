"""Project codec and hashes; schema is loaded once at module import."""

import hashlib
import json
from importlib.resources import files

from jsonschema import Draft202012Validator

from .labels import BOUNDARIES
from .models import Analysis, Mixer, NoiseSpec, PowerSpec, Project, Stage, TwoTone

_VALIDATOR = Draft202012Validator(
    json.loads(files("rf_link_calculator").joinpath("project-v1.schema.json").read_text("utf-8"))
)
_VALIDATOR_V2 = Draft202012Validator(
    json.loads(files("rf_link_calculator").joinpath("project-v2.schema.json").read_text("utf-8"))
)


def canonical(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def project_from_dict(data: dict) -> Project:
    if not isinstance(data, dict) or data.get("schema_version", "1.0.0") not in ("1.0.0", "2.0.0"):
        raise ValueError("仅支持schema_version=1.0.0/2.0.0；保留原文件，禁止覆盖更高版本")
    data = json.loads(canonical(data))  # Detach nested state, normalize tuples, reject NaN.
    validator = _VALIDATOR_V2 if data.get("schema_version") == "2.0.0" else _VALIDATOR
    errors = sorted(validator.iter_errors(data), key=lambda e: str(e.path))
    if errors:
        raise ValueError(
            "输入格式错误：" + "; ".join(f"{'.'.join(map(str, e.path))}: {e.message}" for e in errors[:8])
        )
    a = dict(data.get("analysis", {}))
    a["two_tone"] = TwoTone(**a.get("two_tone", {}))
    stages = []
    for raw in data.get("stages", []):
        s = dict(raw)
        s["noise"] = NoiseSpec(**s.get("noise", {}))
        for k in ("ip3", "p1db"):
            s[k] = PowerSpec(**s.get(k, {}))
        s["mixer"] = Mixer(**s["mixer"]) if s.get("mixer") else None
        s["frequency_range_hz"] = tuple(s["frequency_range_hz"]) if s.get("frequency_range_hz") else None
        stages.append(Stage(**s))
    p = {k: v for k, v in data.items() if k not in ("analysis", "stages", "assumptions")}
    result = Project(
        **p,
        analysis=Analysis(**a),
        stages=tuple(sorted(stages, key=lambda x: x.order)),
        assumptions=tuple(data.get("assumptions", [])),
    )
    if len({s.id for s in stages}) != len(stages) or len({s.order for s in stages}) != len(stages):
        raise ValueError("器件ID或顺序重复")
    if result.lab is not None:
        from rf_link_calculator.advanced.data import validate_lab

        validate_lab(result.lab, {s.id for s in stages})
    for s in stages:
        if s.type in BOUNDARIES and result.lab and result.lab.get("models", {}).get(s.id):
            raise ValueError("标注边界不能挂载射频器件模型")
        if (
            s.enabled
            and s.type in BOUNDARIES
            and (s.gain_db != 0 or any(x.mode != "ideal" for x in (s.noise, s.ip3, s.p1db)))
        ):
            raise ValueError(f"器件 {s.name} ({s.id})：边界仅允许0 dB与显式理想参数；不参与天线或数字域换算")
    return result


def hashes(project: Project) -> tuple[str, str]:
    full = project.to_dict()
    calc = {"analysis": full["analysis"], "stages": []}
    if project.lab:
        model_input = {k: v for k, v in project.lab.items() if k != "reference" and v}
        if "settings" in model_input:
            model_input["settings"] = {
                k: v for k, v in model_input["settings"].items() if k != "tolerance_db"
            }
            if not model_input["settings"]:
                del model_input["settings"]
        if model_input:
            calc["lab"] = model_input
    for s in full["stages"]:
        if s["enabled"]:
            calc["stages"].append(
                {k: v for k, v in s.items() if k not in ("id", "name", "source", "notes", "order")}
            )

    def numeric_normalize(value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value) if value != 0 else 0.0
        if isinstance(value, dict):
            return {k: numeric_normalize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [numeric_normalize(v) for v in value]
        return value

    return tuple(
        hashlib.sha256(canonical(numeric_normalize(d)).encode("utf-8")).hexdigest() for d in (full, calc)
    )
