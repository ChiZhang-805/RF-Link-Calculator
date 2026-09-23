"""Real RF, power-wave harmonic balance for explicit polynomial two-port models.

Positive-frequency Fourier coefficients reconstruct a real wave in sqrt(mW).
b1 = S11*a1 + S12*a2; b2 = S22*a2 + H(f)*FFT(p(a1)*pump).
The pump is 2*cos(LO*t) for mixers. Linear port feedback is eliminated exactly;
Newton-Krylov solves the remaining nonlinear Fourier residual. No noise or
transistor model is inferred from this behavioral model.
"""

import numpy as np
from scipy.fft import next_fast_len
from scipy.optimize import NoConvergence, newton_krylov

from .data import finite, validate_dataset


def linear_block(model, frequency, mixer=False):
    meta = model["metadata"]
    pole = meta.get("pole_hz", 0)
    h = 1 / (1 + 1j * frequency / pole) if pole else 1 + 0j
    gain = model["coefficients"][0]
    return np.array(
        [[meta.get("s11", 0), meta.get("s12", 0)], [0 if mixer else gain * h, meta.get("s22", 0)]], complex
    ), h


def _prepare(project, fundamental_hz, harmonics, source):
    base = finite(fundamental_hz, "基频")
    if base <= 0 or isinstance(harmonics, bool) or int(harmonics) != harmonics or not 2 <= harmonics <= 1024:
        raise ValueError("基频或谐波上限无效")
    k = int(harmonics)
    stages = [s for s in project.stages if s.enabled]
    if not 1 <= len(stages) <= 8:
        raise ValueError("谐波分析支持1～8级")
    models = []
    settings = (project.lab or {}).get("settings", {})
    gamma = [complex(*settings.get(key, [0, 0])) for key in ("source_gamma", "load_gamma")]
    if any(abs(g.imag) > 1e-12 or abs(g) >= 1 for g in gamma):
        raise ValueError("实射频谐波分析需要实数宽带终端反射系数")
    lo_bins = []
    for s in stages:
        spec = (project.lab or {}).get("models", {}).get(s.id, {})
        model = spec.get("harmonic")
        if not model or set(spec) != {"harmonic"}:
            raise ValueError(f"{s.name}：需要独立谐波模型，不能叠加其他模型")
        validate_dataset(model)
        if model.get("metadata", {}).get("amplitude_unit", "sqrt_mw") != "sqrt_mw":
            raise ValueError("谐波模型需要已标定的功率单位")
        models.append(model)
        lo = s.mixer.lo_frequency_hz / base if s.type == "mixer" and s.mixer else 0
        if s.type == "mixer" and (lo <= 0 or abs(lo - round(lo)) > 1e-8 or lo > k):
            raise ValueError(f"{s.name}：LO必须落在谐波栅格内")
        lo_bins.append(round(lo))
    tones = (
        source
        if source is not None
        else [
            {
                "frequency_hz": project.analysis.source_frequency_hz,
                "power_dbm": project.analysis.input_power_dbm,
            }
        ]
    )
    if not isinstance(tones, list) or not 1 <= len(tones) <= 16:
        raise ValueError("载波数应为1～16")
    excitation = np.zeros(k + 1, complex)
    used = set()
    for tone in tones:
        f, p = finite(tone.get("frequency_hz")), finite(tone.get("power_dbm"))
        index = f / base
        phase = finite(tone.get("phase_deg", 0))
        if not 1 <= index <= k or abs(index - round(index)) > 1e-8 or not -300 <= p <= 100:
            raise ValueError("载波必须落在正频率谐波栅格内，且功率在数值范围内")
        index = round(index)
        if index in used:
            raise ValueError("载波频率重复")
        used.add(index)
        excitation[index] = np.sqrt(10 ** (p / 10) * (1 - gamma[0].real ** 2) / 2) * np.exp(
            1j * np.deg2rad(phase)
        )
    return base, k, stages, models, lo_bins, [g.real for g in gamma], excitation, tones


def solve(project, fundamental_hz, harmonics=64, source=None, tolerance=1e-9, max_iterations=80):
    base, k, stages, models, lo_bins, gamma, excitation, tones = _prepare(
        project, fundamental_hz, harmonics, source
    )
    tolerance = finite(tolerance)
    if not 1e-12 <= tolerance <= 1e-5 or not 1 <= max_iterations <= 120:
        raise ValueError("谐波求解容差或迭代上限无效")
    ns, ports = len(stages), 2 * len(stages)
    # Padding resolves the entire polynomial product before retaining the HB band.
    degree = max(len(m["coefficients"]) for m in models)
    n = next_fast_len(2 * (degree * k + max(lo_bins)) + 1)
    t = np.arange(n) / n
    pumps = np.array([2 * np.cos(2 * np.pi * lo * t) if lo else np.ones(n) for lo in lo_bins])
    connection = np.zeros((ports, ports))
    connection[0, 0], connection[-1, -1] = gamma
    for j in range(ns - 1):
        connection[2 * j + 1, 2 * j + 2] = 1
        connection[2 * j + 2, 2 * j + 1] = 1
    linear = np.zeros((k + 1, ports, ports), complex)
    filters = np.zeros((ns, k + 1), complex)
    for j, model in enumerate(models):
        for index in range(k + 1):
            block, filters[j, index] = linear_block(model, index * base, bool(lo_bins[j]))
            linear[index, 2 * j : 2 * j + 2, 2 * j : 2 * j + 2] = block
    matrix = np.eye(ports) - connection @ linear
    if np.max(np.linalg.cond(matrix)) > 1e10:
        raise ValueError("线性端口反馈接近奇异点")
    inverse = np.linalg.inv(matrix)
    rhs = np.zeros((k + 1, ports), complex)
    rhs[:, 0] = excitation
    driven = np.einsum("fij,fj->fi", inverse, rhs)
    response = inverse @ connection[:, 1::2]
    scale = max(np.max(abs(driven)), np.max(abs(excitation)), 1e-15)

    def pack(x):
        return np.concatenate([x[:, :1].real, x[:, 1:].real, x[:, 1:].imag], axis=1).ravel() / scale

    def unpack(z):
        z = z.reshape(ns, 2 * k + 1) * scale
        return np.concatenate([z[:, :1], z[:, 1 : k + 1] + 1j * z[:, k + 1 :]], axis=1)

    def waves(x):
        padded = np.zeros((ns, n // 2 + 1), complex)
        padded[:, : k + 1] = x
        return np.fft.irfft(padded * n, n=n, axis=1)

    def nonlinear(x):
        time = waves(x)
        out = np.zeros_like(x)
        for j, model in enumerate(models):
            coeff = np.r_[0, model["coefficients"]].copy()
            if not lo_bins[j]:
                coeff[1] = 0  # Constant linear gain already in the port matrix.
            y = np.polynomial.polynomial.polyval(time[j], coeff) * pumps[j]
            out[j] = (np.fft.rfft(y) / n)[: k + 1] * filters[j]
        if not np.isfinite(out).all():
            raise ValueError("非线性迭代超出数值范围")
        return out

    def incident(x):
        return driven + np.einsum("fij,jf->fi", response, nonlinear(x))

    def residual(z):
        x = unpack(z)
        return pack(x - incident(x)[:, ::2].T)

    iterations = []
    z = pack(driven[:, ::2].T)
    if np.max(abs(residual(z))) > tolerance:
        try:
            z = newton_krylov(
                residual,
                z,
                f_tol=tolerance,
                maxiter=max_iterations,
                callback=lambda x, r: iterations.append(float(np.max(abs(r)))),
            )
        except (NoConvergence, np.linalg.LinAlgError, ValueError) as exc:
            raise ValueError("谐波平衡未收敛；请调整驱动、模型范围或端口反馈") from exc
    error = float(np.max(abs(residual(z))))
    if not np.isfinite(error) or error > tolerance:
        raise ValueError("谐波平衡残差超限")
    x = unpack(z)
    input_time = waves(x)
    peaks = []
    for j, model in enumerate(models):
        peak = float(np.max(abs(input_time[j])))
        limit = model["metadata"]["max_input"]
        if peak > limit * (1 + 1e-8):
            raise ValueError(f"{stages[j].name}：峰值超出谐波模型输入范围")
        if stages[j].absolute_max_input_dbm is not None and peak**2 > 10 ** (
            stages[j].absolute_max_input_dbm / 10
        ):
            raise ValueError(f"{stages[j].name}：峰值超过最大输入额定值")
        peaks.append(peak)
    a = incident(x)
    b = np.einsum("fij,fj->fi", linear, a)
    b[:, 1::2] += nonlinear(x).T
    output = b[:, -1]
    weight = np.r_[1.0, np.full(k, 2.0)]
    power = abs(output) ** 2 * weight * (1 - gamma[1] ** 2)
    maximum = max(float(np.max(power)), 1e-300)
    selected = set(np.flatnonzero(power >= maximum * 1e-12))
    points = []
    for index in sorted(selected):
        path = [
            {
                "stage_id": stage.id,
                "frequency_hz": index * base,
                "power_dbm": float(10 * np.log10(max(abs(b[index, 2 * j + 1]) ** 2 * weight[index], 1e-300))),
            }
            for j, stage in enumerate(stages)
        ]
        points.append(
            {
                "frequency_hz": index * base,
                "power_dbm": float(10 * np.log10(max(power[index], 1e-300))),
                "phase_deg": float(np.angle(output[index], deg=True)),
                "wave_real": float(output[index].real),
                "wave_imag": float(output[index].imag),
                "product": "DC" if index == 0 else str(index),
                "path": path,
            }
        )
    return {
        "points": points,
        "model": "real_polynomial_power_wave_harmonic_balance_v1",
        "fundamental_hz": base,
        "harmonics": k,
        "samples": n,
        "input_tones": tones,
        "power_convention": "output delivered to real load; per-stage path is the forward outgoing power wave, not net absorbed power",
        "solver": {
            "status": "converged",
            "iterations": len(iterations),
            "relative_residual": error,
            "tolerance": tolerance,
            "history": iterations,
            "input_peaks_sqrt_mw": peaks,
        },
        "scope": "explicit real polynomial two-ports, commensurate tones and pumps, real constant terminations; residual convergence does not certify stability or harmonic truncation",
    }


def convergence(project, fundamental_hz, harmonics=64, source=None, tolerance_db=0.01):
    if harmonics > 512 or not 0 < finite(tolerance_db) <= 0.1:
        raise ValueError("加倍收敛检查需要谐波上限不超过512")
    first = solve(project, fundamental_hz, harmonics, source)
    second = solve(project, fundamental_hz, harmonics * 2, source)
    low = max(p["power_dbm"] for p in first["points"] + second["points"]) - 100
    left = {p["frequency_hz"]: p for p in first["points"]}
    right = {
        p["frequency_hz"]: p for p in second["points"] if p["frequency_hz"] <= fundamental_hz * harmonics
    }
    checks = []
    for frequency in sorted(left.keys() | right.keys()):
        a, b = left.get(frequency), right.get(frequency)
        if max(p["power_dbm"] for p in (a, b) if p is not None) < low:
            continue
        delta = b["power_dbm"] - a["power_dbm"] if a and b else None
        checks.append(
            {
                "frequency_hz": frequency,
                "delta_db": delta,
                "status": "pass" if delta is not None and abs(delta) <= tolerance_db else "fail",
            }
        )
    added = [
        p
        for p in second["points"]
        if p["frequency_hz"] > fundamental_hz * harmonics and p["power_dbm"] >= low
    ]
    second["convergence"] = {
        "status": "pass" if checks and not added and all(r["status"] == "pass" for r in checks) else "fail",
        "harmonic_limits": [harmonics, 2 * harmonics],
        "tolerance_db": tolerance_db,
        "floor_dbm": low,
        "components": checks,
        "new_out_of_band_components": added,
    }
    return second
