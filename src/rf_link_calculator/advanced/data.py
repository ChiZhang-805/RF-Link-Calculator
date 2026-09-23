"""Bounded portable datasets. Imported text is data, never executable code."""

import csv
import hashlib
import io
import json
from pathlib import PurePath

import numpy as np

MAX_POINTS = 8192
MAX_IQ = 32768
KINDS = {"frequency", "sparameter", "ampm", "iq", "imt", "reference", "memory", "harmonic"}


def finite(value, name="数值"):
    if isinstance(value, bool):
        raise ValueError(f"{name}必须是有限数值")
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name}必须是有限数值") from exc
    if not np.isfinite(number):
        raise ValueError(f"{name}必须是有限数值")
    return number


def ascending(values, name):
    a = np.asarray(values, dtype=float)
    if len(a) < 2 or not np.isfinite(a).all() or np.any(np.diff(a) <= 0):
        raise ValueError(f"{name}至少两点，且必须严格递增")


def read_csv(text):
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("表头缺失或重复")
    rows = list(reader)
    if not rows or len(rows) > MAX_IQ or any(None in r for r in rows):
        raise ValueError("数据行数或列数无效")
    return rows


def import_dataset(kind, text, filename="", metadata=None):
    if kind not in KINDS - {"memory"}:
        raise ValueError("未知数据类型")
    if not isinstance(text, str) or len(text.encode("utf-8")) > 8 * 1024 * 1024:
        raise ValueError("数据文件过大")
    meta = dict(metadata or {})
    meta.update(
        filename=PurePath(filename.replace("\\", "/")).name,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    if kind == "sparameter":
        from skrf.io.touchstone import Touchstone

        stream = io.StringIO(text)
        stream.name = "import.ts" if filename.lower().endswith(".ts") else "import.s2p"
        try:
            ts = Touchstone(stream)
            f, s = ts.get_sparameter_arrays()
        except Exception as exc:
            raise ValueError(f"Touchstone读取失败：{exc}") from exc
        if ts.rank != 2 or ts.parameter.lower() != "s":
            raise ValueError("仅接受二端口S参数")
        z0 = np.asarray(ts.z0)
        if np.any(np.abs(z0.imag) > 1e-10) or np.any(z0.real <= 0):
            raise ValueError("参考阻抗必须为正实数")
        data = {
            "kind": kind,
            "frequency_hz": f.tolist(),
            "s_real": s.real.tolist(),
            "s_imag": s.imag.tolist(),
            "z0_ohm": z0.real.tolist(),
            "metadata": meta,
        }
        if ts.noise is not None:
            n = ts.noise
            # Touchstone 1.x normalizes Rn to the reference impedance; 2.x uses ohms.
            if ts.version == "1.0" and not np.allclose(z0, z0[0, 0]):
                raise ValueError("含噪声参数的v1文件要求统一参考阻抗")
            data["noise"] = {
                "frequency_hz": n[:, 0].tolist(),
                "nfmin_db": n[:, 1].tolist(),
                "gamma_mag": n[:, 2].tolist(),
                "gamma_deg": n[:, 3].tolist(),
                "rn_ohm": (n[:, 4] * (z0[0, 0].real if ts.version == "1.0" else 1)).tolist(),
            }
    elif kind == "harmonic":
        rows = read_csv(text)
        if not {"order", "coefficient"}.issubset(rows[0]):
            raise ValueError("谐波模型需要order,coefficient列")
        orders = [finite(r["order"]) for r in rows]
        if orders != list(range(1, len(rows) + 1)):
            raise ValueError("谐波阶数必须从1连续递增")
        data = {"kind": kind, "coefficients": [finite(r["coefficient"]) for r in rows], "metadata": meta}
    elif kind == "reference":
        rows = read_csv(text)
        required = {"scope", "metric", "value", "unit", "status"}
        if not required.issubset(rows[0]):
            raise ValueError("对标表需要scope,metric,value,unit,status列")
        data = {"kind": kind, "rows": rows, "metadata": meta}
    else:
        rows = read_csv(text)
        columns = {
            "frequency": ("frequency_hz", "gain_db"),
            "ampm": ("pin_dbm", "pout_dbm", "phase_deg"),
            "iq": ("i_in", "q_in", "i_out", "q_out"),
            "imt": ("m", "n", "output_dbm"),
        }[kind]
        if not set(columns).issubset(rows[0]):
            raise ValueError("需要列：" + ",".join(columns))
        allowed = set(columns) | (
            {"nf_db", "iip3_dbm", "ip1_dbm", "phase_deg"} if kind == "frequency" else set()
        )
        data = {"kind": kind, "metadata": meta}
        for key in allowed & set(rows[0]):
            data[key] = [finite(r[key], key) for r in rows]
    validate_dataset(data)
    return data


def validate_dataset(data):
    if not isinstance(data, dict) or data.get("kind") not in KINDS:
        raise ValueError("模型数据类型无效")
    kind = data["kind"]
    if not isinstance(data.get("metadata", {}), dict):
        raise ValueError("数据来源元信息必须为对象")
    # Reject NaN/Infinity, objects masquerading as arrays and oversized embedded payloads.
    if len(json.dumps(data, allow_nan=False, ensure_ascii=False)) > 12 * 1024 * 1024:
        raise ValueError("单个模型数据过大")
    if kind == "harmonic":
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in data.get("coefficients", [])):
            raise ValueError("实射频系数必须是数值")
        c = np.asarray(data.get("coefficients", []), dtype=float)
        if (
            c.ndim != 1
            or not 1 <= len(c) <= 9
            or not np.isfinite(c).all()
            or not 0 < c[0] <= 1e6
            or np.max(abs(c)) > 1e12
        ):
            raise ValueError("实射频多项式系数无效")
        meta = data.get("metadata", {})
        for key in ("max_input", "s11", "s12", "s22", "pole_hz"):
            if key in meta and (isinstance(meta[key], bool) or not isinstance(meta[key], (int, float))):
                raise ValueError("谐波模型参数必须是数值")
        if not 0 < finite(meta.get("max_input"), "输入峰值") <= 1e6:
            raise ValueError("谐波模型需要有效输入峰值")
        for key in ("s11", "s22"):
            if abs(finite(meta.get(key, 0))) >= 1:
                raise ValueError("模型端口反射系数模必须小于1")
        if abs(finite(meta.get("s12", 0))) > 1 or not 0 <= finite(meta.get("pole_hz", 0)) <= 1e15:
            raise ValueError("反向传输或极点频率无效")
        return
    if kind == "memory":
        orders, depth = data.get("orders", []), data.get("depth", 0)
        if (
            1 not in orders
            or any(k not in (1, 3, 5, 7, 9) for k in orders)
            or len(set(orders)) != len(orders)
        ):
            raise ValueError("记忆多项式阶数无效")
        if not isinstance(depth, int) or not 1 <= depth <= 8:
            raise ValueError("记忆深度应为1～8")
        c = np.asarray(data.get("coefficients"), dtype=float)
        if c.shape != (len(orders) * depth, 2) or not np.isfinite(c).all():
            raise ValueError("记忆多项式系数无效")
        for key in ("sample_rate_hz", "scale", "max_input"):
            if finite(data.get(key), key) <= 0:
                raise ValueError(f"{key}应为正数")
        return
    if kind == "reference":
        rows = data.get("rows", [])
        if not 0 < len(rows) <= 20000:
            raise ValueError("对标行数无效")
        for row in rows:
            if not {"scope", "metric", "value", "unit", "status"}.issubset(row):
                raise ValueError("对标列缺失")
            if row["value"] != "":
                finite(row["value"], "对标值")
        return
    key = {
        "frequency": "frequency_hz",
        "sparameter": "frequency_hz",
        "ampm": "pin_dbm",
        "iq": "i_in",
        "imt": "m",
    }[kind]
    n = len(data.get(key, []))
    if not 1 <= n <= (MAX_IQ if kind == "iq" else MAX_POINTS):
        raise ValueError("模型数据点数无效")
    for k, values in data.items():
        if k in ("kind", "metadata", "noise"):
            continue
        a = np.asarray(values, dtype=float)
        if a.ndim == 0 or len(a) != n or not np.isfinite(a).all():
            raise ValueError(f"{k}长度或数值无效")
    if kind in ("frequency", "sparameter", "ampm"):
        ascending(data[key], key)
    required = {
        "frequency": ("gain_db",),
        "sparameter": ("s_real", "s_imag", "z0_ohm"),
        "ampm": ("pout_dbm", "phase_deg"),
        "iq": ("q_in", "i_out", "q_out"),
        "imt": ("n", "output_dbm"),
    }[kind]
    if not all(k in data for k in required):
        raise ValueError("模型数据列缺失")
    if kind in ("frequency", "sparameter") and min(data[key]) <= 0:
        raise ValueError("频率必须为正数")
    if kind == "sparameter":
        if np.shape(data["s_real"]) != (n, 2, 2) or np.shape(data["s_imag"]) != (n, 2, 2):
            raise ValueError("S参数必须为2×2矩阵")
        if np.shape(data["z0_ohm"]) != (n, 2) or np.min(data["z0_ohm"]) <= 0:
            raise ValueError("参考阻抗无效")
        if data.get("noise"):
            noise = data["noise"]
            ascending(noise.get("frequency_hz", []), "噪声频率")
            for k in ("nfmin_db", "gamma_mag", "gamma_deg", "rn_ohm"):
                if len(noise.get(k, [])) != len(noise["frequency_hz"]) or not np.isfinite(noise[k]).all():
                    raise ValueError("噪声参数长度或数值无效")
            if (
                min(noise["nfmin_db"]) < 0
                or min(noise["rn_ohm"]) < 0
                or not all(0 <= g < 1 for g in noise["gamma_mag"])
            ):
                raise ValueError("噪声参数超出物理范围")
    if kind == "frequency" and "nf_db" in data and min(data["nf_db"]) < 0:
        raise ValueError("噪声系数不能为负")
    if kind == "ampm" and np.any(np.diff(data["pout_dbm"]) < 0):
        raise ValueError("AM/AM输出功率必须单调不减")
    if kind == "imt":
        if any(int(v) != v or abs(v) > 20 for k in ("m", "n") for v in data[k]):
            raise ValueError("混频阶数必须为-20～20整数")
        for k in ("rf_hz", "lo_hz", "rf_dbm", "lo_dbm"):
            finite(data.get("metadata", {}).get(k), k)


def validate_lab(lab, ids):
    if not isinstance(lab, dict) or set(lab) - {"models", "settings", "reference", "iq", "circuit"}:
        raise ValueError("高级分析字段无效")
    if "circuit" in lab:
        from .circuit import validate

        validate(lab["circuit"])
    models = lab.get("models", {})
    if not isinstance(models, dict) or set(models) - ids:
        raise ValueError("模型引用了不存在的器件")
    for spec in models.values():
        if not isinstance(spec, dict) or set(spec) - {"linear", "nonlinear", "mixer", "harmonic"}:
            raise ValueError("器件模型字段无效")
        for slot, ds in spec.items():
            validate_dataset(ds)
            if ds.get("metadata", {}).get("amplitude_unit", "sqrt_mw") != "sqrt_mw":
                raise ValueError("器件模型需要已标定的功率单位")
            allowed = {
                "linear": {"frequency", "sparameter"},
                "nonlinear": {"ampm", "memory"},
                "mixer": {"imt"},
                "harmonic": {"harmonic"},
            }
            if ds["kind"] not in allowed[slot]:
                raise ValueError("模型槽位不匹配")
        if "harmonic" in spec and len(spec) > 1:
            raise ValueError("谐波模型不能与其他器件模型叠加")
    for name in ("reference", "iq"):
        if lab.get(name):
            validate_dataset(lab[name])
            if name == "iq" and lab[name].get("metadata", {}).get("amplitude_unit", "sqrt_mw") != "sqrt_mw":
                raise ValueError("链路IQ需要已标定的功率单位")
            if lab[name]["kind"] != name:
                raise ValueError("分析数据类型不匹配")
    settings = lab.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("分析条件无效")
    for name in ("source_gamma", "load_gamma"):
        if name in settings:
            g = settings[name]
            if not isinstance(g, list) or len(g) != 2 or abs(complex(*map(finite, g))) >= 1:
                raise ValueError("源与负载反射系数模必须小于1")


def sample(data, x, key, axis="frequency_hz"):
    xs = np.asarray(data[axis])
    x = np.asarray(x)
    if np.any(x < xs[0]) or np.any(x > xs[-1]):
        raise ValueError(f"{key}超出数据范围 [{xs[0]:g}, {xs[-1]:g}]")
    ys = np.asarray(data[key])
    if key == "phase_deg":
        ys = np.rad2deg(np.unwrap(np.deg2rad(ys)))
    return np.interp(x, xs, ys)
