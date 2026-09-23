"""Complex two-port cascade and noise-wave covariance at a single frequency.

Power waves use real reference impedances. C is in W/Hz, not normalized NF.
Frequency conversion is a unilateral selected-product block; no harmonic balance.
"""

import json
from dataclasses import replace
from functools import lru_cache

import numpy as np
import skrf as rf

from rf_link_calculator.domain.models import NoiseSpec, PowerSpec
from rf_link_calculator.engine.frequency import map_frequency
from rf_link_calculator.engine.noise import added_noise

from .data import sample

KB = 1.380649e-23
T0 = 290.0
THRU = np.array([[0, 1], [1, 0]], complex)


def db(value):
    return float(10 * np.log10(max(float(value), 1e-300)))


@lru_cache(maxsize=64)
def normalized_arrays(raw):
    data = json.loads(raw)
    f = np.asarray(data["frequency_hz"])
    s = np.asarray(data["s_real"]) + 1j * np.asarray(data["s_imag"])
    nt = rf.Network(f=f, s=s, z0=np.asarray(data["z0_ohm"]), f_unit="hz")
    nt.renormalize(50)
    return f, nt.s


def s_at(data, frequency):
    # Renormalize every original frequency before interpolation. Interpolating dB/phase
    # can fabricate transmission near a zero; use complex Cartesian interpolation.
    sample(data, frequency, "frequency_hz")
    raw = json.dumps(
        {k: data[k] for k in ("frequency_hz", "s_real", "s_imag", "z0_ohm")}, separators=(",", ":")
    )
    f, s = normalized_arrays(raw)
    return np.array([[np.interp(frequency, f, s[:, i, j]) for j in range(2)] for i in range(2)])


def noise_wave(data, s, frequency):
    n = data["noise"]
    if not np.allclose(data["z0_ohm"], np.asarray(data["z0_ohm"])[0, 0]):
        raise ValueError("噪声参数需要统一参考阻抗")
    z = float(data["z0_ohm"][0][0])
    fmin = 10 ** (float(sample(n, frequency, "nfmin_db")) / 10)
    optimal = np.asarray(n["gamma_mag"]) * np.exp(1j * np.deg2rad(n["gamma_deg"]))
    gamma = np.interp(frequency, n["frequency_hz"], optimal)
    rn = float(sample(n, frequency, "rn_ohm"))
    yopt = (1 - gamma) / (z * (1 + gamma))
    # Voltage/current covariance is independent of the wave reference impedance.
    a = (
        4
        * KB
        * T0
        * np.array(
            [[rn, (fmin - 1) / 2 - rn * yopt.conjugate()], [(fmin - 1) / 2 - rn * yopt, rn * abs(yopt) ** 2]]
        )
    )
    if abs(s[1, 0]) < 1e-15:
        raise ValueError("S21接近零，噪声参数转换奇异")
    transform = np.array(
        [
            [np.sqrt(50), -np.sqrt(50) * (1 + s[0, 0]) / s[1, 0]],
            [-1 / np.sqrt(50), -(1 - s[0, 0]) / (np.sqrt(50) * s[1, 0])],
        ]
    )
    inv = np.linalg.inv(transform)
    c = inv @ a @ inv.conj().T
    if np.linalg.eigvalsh(c).min() < -1e-8 * max(np.linalg.norm(c), KB * T0):
        raise ValueError("噪声参数产生非物理协方差")
    return c


def stage_at(stage, spec, frequency):
    if spec.get("harmonic"):
        from .harmonic import linear_block

        output, _ = map_frequency(stage, frequency)
        if output is None or output <= 0:
            raise ValueError("谐波模型输出频率无效")
        s, _ = linear_block(spec["harmonic"], output)
        c = np.zeros((2, 2), complex) if stage.noise.mode == "ideal" else None
        updated = replace(
            stage,
            gain_db=db(abs(s[1, 0]) ** 2),
            noise=NoiseSpec("ideal" if c is not None else "unknown"),
            ip3=PowerSpec("unknown"),
            p1db=PowerSpec("unknown"),
        )
        return s, c, updated
    linear = spec.get("linear")
    updated = stage
    phase = 0.0
    if linear and linear["kind"] == "frequency":
        gain = float(sample(linear, frequency, "gain_db"))
        changes = {"gain_db": gain}
        if "nf_db" in linear:
            changes["noise"] = NoiseSpec("manual", float(sample(linear, frequency, "nf_db")))
        for key, slot in (("iip3_dbm", "ip3"), ("ip1_dbm", "p1db")):
            if key in linear:
                changes[slot] = PowerSpec("finite", "input", float(sample(linear, frequency, key)))
        if "phase_deg" in linear:
            phase = float(sample(linear, frequency, "phase_deg"))
        updated = replace(stage, **changes)
    nonlinear = spec.get("nonlinear")
    if nonlinear and not linear:
        from .nonlinear import model_p1

        if nonlinear["kind"] == "ampm":
            updated = replace(updated, gain_db=nonlinear["pout_dbm"][0] - nonlinear["pin_dbm"][0])
        elif nonlinear["kind"] == "memory":
            from .nonlinear import small_signal

            updated = replace(updated, gain_db=db(abs(small_signal(nonlinear)) ** 2))
        p1 = model_p1(nonlinear)
        updated = replace(updated, p1db=PowerSpec("unknown" if p1 is None else "finite", "input", p1))
    if linear and linear["kind"] == "sparameter":
        s = s_at(linear, frequency)
        updated = replace(updated, gain_db=db(abs(s[1, 0]) ** 2))
        if linear.get("noise"):
            c = noise_wave(linear, s, frequency)
        elif stage.noise.mode == "passive_thermal":
            loss = np.eye(2) - s @ s.conj().T
            if np.linalg.eigvalsh(loss).min() < -1e-7:
                raise ValueError(f"{stage.name}：热无源S参数不满足无源性")
            c = KB * stage.physical_temperature_k * loss
        elif stage.noise.mode == "ideal":
            c = np.zeros((2, 2), complex)
        else:
            c = None  # A scalar NF does not determine all noise correlations.
        nf = terminated(s, c)["nf_db"]
        updated = replace(updated, noise=NoiseSpec("unknown" if nf is None else "manual", nf))
    else:
        if updated.gain_db is None or abs(updated.gain_db) > 600:
            raise ValueError(f"{stage.name}：增益缺失或超出数值范围")
        gain = 10 ** (updated.gain_db / 10)
        s = np.array([[0, 0], [np.sqrt(gain) * np.exp(1j * np.deg2rad(phase)), 0]])
        extra = added_noise(updated)
        c = None if extra is None else np.diag([0, KB * T0 * gain * extra]).astype(complex)
    return s, c, updated


def cascade(a, ca, b, cb):
    d = 1 - a[1, 1] * b[0, 0]
    if abs(d) < 1e-12:
        raise ValueError("级间反馈接近奇异点")
    s = np.array(
        [
            [a[0, 0] + a[0, 1] * b[0, 0] * a[1, 0] / d, a[0, 1] * b[0, 1] / d],
            [b[1, 0] * a[1, 0] / d, b[1, 1] + b[1, 0] * a[1, 1] * b[0, 1] / d],
        ]
    )
    if ca is None or cb is None:
        return s, None
    la = np.array([[1, a[0, 1] * b[0, 0] / d], [0, b[1, 0] / d]])
    lb = np.array([[a[0, 1] / d, 0], [b[1, 0] * a[1, 1] / d, 1]])
    return s, la @ ca @ la.conj().T + lb @ cb @ lb.conj().T


def terminated(s, c, gs=0j, gl=0j):
    d = (1 - s[0, 0] * gs) * (1 - s[1, 1] * gl) - s[0, 1] * s[1, 0] * gs * gl
    if abs(d) < 1e-12:
        raise ValueError("源/负载反馈接近奇异点")
    gt = (1 - abs(gs) ** 2) * (1 - abs(gl) ** 2) * abs(s[1, 0] / d) ** 2
    if gt <= 1e-280 or not np.isfinite(gt):
        raise ValueError("传输增益接近零或超出数值范围")
    noise = None
    if c is not None:
        m = np.linalg.inv(np.eye(2) - s @ np.diag([gs, gl]))
        noise = max(0.0, float((m @ c @ m.conj().T)[1, 1].real) * (1 - abs(gl) ** 2))
    return {
        "gain_db": db(gt),
        "nf_db": None if noise is None else db(1 + noise / (KB * T0 * gt)),
        "added_noise_w_hz": noise,
        "phase_deg": float(np.angle(s[1, 0] / d, deg=True)),
        "s11_db": None if s[0, 0] == 0 else db(abs(s[0, 0]) ** 2),
        "s22_db": None if s[1, 1] == 0 else db(abs(s[1, 1]) ** 2),
        "s21_real": float(s[1, 0].real),
        "s21_imag": float(s[1, 0].imag),
    }


def evaluate(project, frequency=None):
    lab = project.lab or {}
    settings, models = lab.get("settings", {}), lab.get("models", {})
    gs, gl = (complex(*settings.get(k, [0, 0])) for k in ("source_gamma", "load_gamma"))
    f = frequency if frequency is not None else project.analysis.source_frequency_hz
    s, c = THRU.copy(), np.zeros((2, 2), complex)
    rows, stages = [], []
    phase_known = True

    def reported_values():
        values = terminated(s, c, gs, gl)
        if not phase_known:
            # Zero phase permits scalar power propagation but is not measured phase data.
            for key in ("phase_deg", "s21_real", "s21_imag"):
                values[key] = None
        return values

    for stage in project.stages:
        fin = f
        if stage.enabled:
            spec = models.get(stage.id, {})
            linear = spec.get("linear", {})
            phase_known = phase_known and (
                linear.get("kind") == "sparameter" or "phase_deg" in linear or bool(spec.get("harmonic"))
            )
            block, nc, projected = stage_at(stage, spec, f)
            s, c = cascade(s, c, block, nc)
            f, _ = map_frequency(stage, f)
            if f is None or f <= 0:
                raise ValueError(f"{stage.name}：变频输出无效")
        else:
            projected = stage
        values = reported_values()
        rows.append({"stage_id": stage.id, "frequency_input_hz": fin, "frequency_output_hz": f, **values})
        stages.append(projected)
    return {
        "frequency_hz": frequency or project.analysis.source_frequency_hz,
        "frequency_output_hz": f,
        **reported_values(),
        "stages": rows,
    }, tuple(stages)


def sweep(project, low, high, count=201):
    if not 0 < low < high or not 2 <= count <= 801:
        raise ValueError("扫频范围或点数无效")
    rows = []
    for f in np.linspace(low, high, count):
        try:
            values, _ = evaluate(project, float(f))
            values.pop("stages")
            rows.append({**values, "status": "valid"})
        except ValueError as exc:
            rows.append({"frequency_hz": float(f), "status": "unknown", "reason": str(exc)})
    return {"points": rows, "model": "complex_two_port_noise_wave_v2"}


def integrated_noise(project, low, high, count=401):
    if not 0 < low < high or not 3 <= count <= 801:
        raise ValueError("噪声积分范围或点数无效")
    points = []
    frequencies = np.linspace(low, high, count)
    densities = []
    for f in frequencies:
        values, _ = evaluate(project, float(f))
        if values["nf_db"] is None:
            raise ValueError("积分频带内噪声数据不完整")
        gt = 10 ** (values["gain_db"] / 10)
        density = KB * project.analysis.source_noise_temperature_k * gt + values["added_noise_w_hz"]
        densities.append(density)
        points.append(
            {
                "frequency_hz": float(f),
                "frequency_output_hz": values["frequency_output_hz"],
                "power_dbm": db(density * 1000),
                "path": [],
            }
        )
    # Only a monotonic selected mixing product is integrated here. An image band has
    # its own termination and must not be silently counted a second time.
    mapped = np.array([p["frequency_output_hz"] for p in points])
    if not (np.all(np.diff(mapped) > 0) or np.all(np.diff(mapped) < 0)):
        raise ValueError("积分频带跨越混频折叠点")
    power = float(np.trapezoid(densities, frequencies))
    return {
        "points": points,
        "integrated_noise_dbm": db(power * 1000),
        "bandwidth_hz": high - low,
        "model": "noise_wave_band_integration_v2",
    }
