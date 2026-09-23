"""Bounded RC / intrinsic Ebers-Moll NPN circuit HB and white periodic noise.

All electrical quantities are SI. Fixed junction capacitors are ordinary linear
elements, not a charge-storage / full Gummel-Poon model. Noise is a small random
perturbation about the large periodic orbit, with two-sided PSD internally.
"""

import re
from copy import deepcopy

import numpy as np
from scipy.integrate import solve_ivp
from scipy.linalg import solve as linear_solve

KB = 1.380649e-23
QE = 1.602176634e-19
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")


def number(x, label, low, high):
    if isinstance(x, bool) or not isinstance(x, (float, int)) or not np.isfinite(x) or not low <= x <= high:
        raise ValueError(f"{label}超出范围")
    return float(x)


def fields(obj, required, optional=()):
    if not isinstance(obj, dict) or set(obj) - set(required) - set(optional) or not set(required) <= set(obj):
        raise ValueError("电路字段缺失或不支持")


def _validate(circuit):
    fields(circuit, ("version", "temperature_k", "models", "components", "output_resistor"), ("metadata",))
    if type(circuit["version"]) is not int or circuit["version"] != 1:
        raise ValueError("电路版本无效")
    number(circuit["temperature_k"], "温度", 200, 400)
    models = circuit["models"]
    if not isinstance(models, dict) or len(models) > 4:
        raise ValueError("晶体管模型数量无效")
    if len({str(k).lower() for k in models}) != len(models):
        raise ValueError("模型名称不能仅大小写不同")
    for name, m in models.items():
        if not NAME.fullmatch(name):
            raise ValueError("模型名称无效")
        fields(m, ("type", "is_a", "bf", "br", "cbe_f", "cbc_f", "max_current_a", "max_vbe_v", "max_vce_v"))
        if m["type"] != "npn_ebers_moll":
            raise ValueError("晶体管模型不支持")
        for key, low, high in (
            ("is_a", 1e-25, 1e-6),
            ("bf", 1, 1e4),
            ("br", 0.1, 1e4),
            ("cbe_f", 0, 1e-3),
            ("cbc_f", 0, 1e-3),
            ("max_current_a", 1e-9, 10),
            ("max_vbe_v", 0.1, 1.5),
            ("max_vce_v", 1, 100),
        ):
            number(m[key], key, low, high)
    elements = circuit["components"]
    if not isinstance(elements, list) or not 1 <= len(elements) <= 32:
        raise ValueError("电路支持1～32个元件")
    ids, nodes, fixed = set(), {"0"}, {"0"}
    counts = {"npn": 0}
    for e in elements:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not NAME.fullmatch(e["id"]):
            raise ValueError("元件名称无效")
        if e["id"].lower() in ids:
            raise ValueError("元件名称重复")
        ids.add(e["id"].lower())
        kind = e.get("type")
        if kind in ("resistor", "capacitor"):
            fields(e, ("id", "type", "p", "n", "value"), ("temperature_k",) if kind == "resistor" else ())
            number(
                e["value"],
                "阻值" if kind == "resistor" else "电容",
                1e-3 if kind == "resistor" else 1e-18,
                1e12 if kind == "resistor" else 1,
            )
            if "temperature_k" in e:
                number(e["temperature_k"], "电阻温度", 0, 2000)
        elif kind == "voltage":
            fields(e, ("id", "type", "p", "n", "dc_v", "tones"))
            if e["n"] != "0" or e["p"] in fixed:
                raise ValueError("电压源必须接地且驱动节点不能重复")
            fixed.add(e["p"])
            number(e["dc_v"], "直流电压", -100, 100)
            if not isinstance(e["tones"], list) or len(e["tones"]) > 8:
                raise ValueError("电压源载波数无效")
            used = set()
            for t in e["tones"]:
                fields(t, ("harmonic", "peak_v", "phase_deg"))
                if type(t["harmonic"]) is not int or not 1 <= t["harmonic"] <= 32 or t["harmonic"] in used:
                    raise ValueError("载波谐波序号无效或重复")
                used.add(t["harmonic"])
                number(t["peak_v"], "峰值电压", 0, 10)
                number(t["phase_deg"], "相位", -36000, 36000)
        elif kind == "npn":
            fields(e, ("id", "type", "b", "c", "e", "model"))
            if e["model"] not in models or len({e["b"], e["c"], e["e"]}) != 3:
                raise ValueError("晶体管连接或模型无效")
            counts["npn"] += 1
        else:
            raise ValueError("电路元件类型不支持")
        pins = ("b", "c", "e") if kind == "npn" else ("p", "n")
        if kind != "npn" and e["p"] == e["n"]:
            raise ValueError("元件两端不能相同")
        for pin in pins:
            node = e[pin]
            if not isinstance(node, str) or (node != "0" and not NAME.fullmatch(node)):
                raise ValueError("节点名称无效")
            nodes.add(node)
    if counts["npn"] > 4 or not 1 <= len(nodes - fixed) <= 6 or len(nodes) > 12:
        raise ValueError("电路最多4个晶体管、6个未知节点、12个节点")
    if len({s.lower() for s in nodes}) != len(nodes):
        raise ValueError("节点名称不能仅大小写不同")
    if not any(e["id"] == circuit["output_resistor"] and e["type"] == "resistor" for e in elements):
        raise ValueError("输出必须选择已有电阻")
    meta = circuit.get("metadata", {})
    if not isinstance(meta, dict) or any(not isinstance(v, str) or len(v) > 2048 for v in meta.values()):
        raise ValueError("电路来源字段无效")


def validate(circuit):
    try:
        _validate(circuit)
    except (TypeError, KeyError, AttributeError) as exc:
        raise ValueError("电路字段类型无效") from exc


class Circuit:
    def __init__(self, spec):
        validate(spec)
        self.spec = deepcopy(spec)
        elements = spec["components"]
        self.nodes = sorted(
            {e[p] for e in elements for p in (("b", "c", "e") if e["type"] == "npn" else ("p", "n"))}
        )
        self.index = {s: i for i, s in enumerate(self.nodes)}
        self.sources = [e for e in elements if e["type"] == "voltage"]
        fixed = {"0"} | {e["p"] for e in self.sources}
        self.unknown = [self.index[s] for s in self.nodes if s not in fixed]
        self.size, self.nu = len(self.nodes), len(self.unknown)
        self.g = np.zeros((self.size, self.size))
        self.cap = self.g.copy()
        self.q = []
        self.resistors = []
        self.vt = KB * spec["temperature_k"] / QE
        for e in elements:
            if e["type"] in ("resistor", "capacitor"):
                vector = self.branch(e["p"], e["n"])
                if e["type"] == "resistor":
                    self.g += np.outer(vector, vector) / e["value"]
                    self.resistors.append((e, vector))
                else:
                    self.cap += np.outer(vector, vector) * e["value"]
            elif e["type"] == "npn":
                m = spec["models"][e["model"]]
                be, bc, ce = (
                    self.branch(e["b"], e["e"]),
                    self.branch(e["b"], e["c"]),
                    self.branch(e["c"], e["e"]),
                )
                self.cap += m["cbe_f"] * np.outer(be, be) + m["cbc_f"] * np.outer(bc, bc)
                self.q.append((e, m, be, bc, ce))
        self.output, self.ov = next((e, v) for e, v in self.resistors if e["id"] == spec["output_resistor"])

    def branch(self, p, n):
        v = np.zeros(self.size)
        v[self.index[p]], v[self.index[n]] = 1, -1
        return v

    def device(self, voltage):
        """KCL currents and exact conductance Jacobian at each sample."""
        voltage = np.atleast_2d(voltage)
        current = np.zeros_like(voltage)
        jac = np.zeros((len(voltage), self.size, self.size))
        for _, m, be, bc, ce in self.q:
            xf, xr = voltage @ be / self.vt, voltage @ bc / self.vt
            if max(np.max(xf), np.max(xr)) > 80:
                raise ValueError("晶体管迭代电压超出数值范围")
            ef, er = np.exp(xf), np.exp(xr)
            forward, reverse = m["is_a"] * np.expm1(xf), m["is_a"] * np.expm1(xr)
            # IF flows C->E plus IF/BF at B->E; IR flows E->C plus IR/BR at B->C.
            sf, sr = ce + be / m["bf"], -ce + bc / m["br"]
            current += forward[:, None] * sf + reverse[:, None] * sr
            jac += (m["is_a"] / self.vt) * (
                ef[:, None, None] * np.outer(sf, be) + er[:, None, None] * np.outer(sr, bc)
            )
        return current, jac

    def check_limits(self, time):
        for e, m, be, bc, ce in self.q:
            if (
                np.max(abs(time @ be)) > m["max_vbe_v"]
                or np.max(abs(time @ bc)) > m["max_vce_v"]
                or np.max(abs(time @ ce)) > m["max_vce_v"]
            ):
                raise ValueError(e["id"] + "：结电压超出模型范围")
            transport = m["is_a"] * (np.exp(time @ be / self.vt) - np.exp(time @ bc / self.vt))
            if np.max(abs(transport)) > m["max_current_a"]:
                raise ValueError(e["id"] + "：电流超出模型范围")


def _newton(fun, x, atol=1e-11, maxiter=90):
    history = []
    for _ in range(maxiter):
        r, jac = fun(x)
        error = float(np.max(abs(r)))
        history.append(error)
        voltage_error = float(np.max(abs(r) / np.maximum(np.max(abs(jac), axis=1), 1e-30)))
        if error <= atol and voltage_error <= 1e-9:
            scaled = jac / np.maximum(np.max(abs(jac), axis=1), 1e-30)[:, None]
            if np.linalg.cond(scaled) > 1e12:
                raise ValueError("电路矩阵奇异；请检查浮空节点和连接")
            return x, history
        try:
            step = linear_solve(jac, -r, assume_a="gen", check_finite=True)
        except (ValueError, np.linalg.LinAlgError) as exc:
            raise ValueError("电路矩阵奇异；请检查浮空节点和连接") from exc
        # Limit each trial voltage change; no artificial conductance is retained.
        damping = min(1.0, 0.1 / max(np.max(abs(step)), 1e-30))
        for _ in range(24):
            candidate = x + damping * step
            try:
                rr, _ = fun(candidate)
                if np.max(abs(rr)) < error:
                    x = candidate
                    break
            except ValueError:
                pass
            damping /= 2
        else:
            raise ValueError("电路牛顿迭代未收敛")
    raise ValueError("电路牛顿迭代次数超限")


def _dc(c):
    v = np.zeros(c.size)
    for source in c.sources:
        v[c.index[source["p"]]] = source["dc_v"]

    def residual(x):
        trial = v.copy()
        trial[c.unknown] = x
        current, jac = c.device(trial)
        return (c.g @ trial + current[0])[c.unknown], (c.g + jac[0])[np.ix_(c.unknown, c.unknown)]

    x, history = _newton(residual, np.zeros(c.nu))
    v[c.unknown] = x
    c.check_limits(v[None, :])
    return v, history


def operating_point(spec):
    c = Circuit(spec)
    v, history = _dc(c)
    devices = []
    for e, m, be, bc, ce in c.q:
        f, r = m["is_a"] * np.expm1(v @ be / c.vt), m["is_a"] * np.expm1(v @ bc / c.vt)
        devices.append(
            {
                "id": e["id"],
                "vbe_v": float(v @ be),
                "vce_v": float(v @ ce),
                "ic_a": float(f - (1 + 1 / m["br"]) * r),
                "ib_a": float(f / m["bf"] + r / m["br"]),
            }
        )
    return {
        "model": "intrinsic_ebers_moll_rc_v1",
        "nodes": [{"node": name, "voltage_v": float(v[j])} for j, name in enumerate(c.nodes)],
        "devices": devices,
        "solver": {"status": "converged", "residual_a": history[-1], "iterations": len(history) - 1},
    }


def _basis(k, n):
    angle = 2 * np.pi * np.arange(n)[:, None] * np.arange(1, k + 1)[None, :] / n
    b = np.c_[np.ones(n), 2 * np.cos(angle), -2 * np.sin(angle)]
    e = b.T.copy() / (2 * n)
    e[0] *= 2
    return b, e


def _coeff(z, k):
    return np.c_[z[:, 0], z[:, 1 : k + 1] + 1j * z[:, k + 1 :]]


def _hb(spec, fundamental_hz, harmonics, oversampling=8):
    base = number(fundamental_hz, "基频", 1, 1e11)
    if type(harmonics) is not int or not 1 <= harmonics <= 32:
        raise ValueError("电路谐波阶数应为1～32")
    c = Circuit(spec)
    k, width = harmonics, 2 * harmonics + 1
    n = oversampling * width
    b, e = _basis(k, n)
    dc, dc_history = _dc(c)
    z = np.zeros((c.size, width))
    z[:, 0] = dc
    for source in c.sources:
        for tone in source["tones"]:
            index = tone["harmonic"]
            if index > k:
                raise ValueError("电压源载波超出谐波上限")
            phasor = tone["peak_v"] / 2 * np.exp(1j * np.deg2rad(tone["phase_deg"]))
            z[c.index[source["p"]], index] = phasor.real
            z[c.index[source["p"]], k + index] = phasor.imag
    derivative = np.zeros((width, width))
    for index in range(1, k + 1):
        derivative[index, k + index] = -2 * np.pi * base * index
        derivative[k + index, index] = 2 * np.pi * base * index
    # Ordering is node, real Fourier coordinate.
    linear = np.kron(c.g, np.eye(width)) + np.kron(c.cap, derivative)
    selection = np.array([i * width + j for i in c.unknown for j in range(width)])
    jac_linear = linear[np.ix_(selection, selection)]

    def residual(x):
        trial = z.copy()
        trial[c.unknown] = x.reshape(c.nu, width)
        time = b @ trial.T
        current, jac = c.device(time)
        r = linear @ trial.ravel() + (e @ current).T.ravel()
        j = np.einsum("an,nij,nb->iajb", e, jac[:, c.unknown][:, :, c.unknown], b, optimize=True).reshape(
            c.nu * width, c.nu * width
        )
        return r[selection], j + jac_linear

    x, history = _newton(residual, z[c.unknown].ravel())
    z[c.unknown] = x.reshape(c.nu, width)
    time = b @ z.T
    c.check_limits(time)
    # Check the same solution on a doubled quadrature grid to expose FFT aliasing.
    bb, ee = _basis(k, 2 * n)
    current, _ = c.device(bb @ z.T)
    alias_error = float(np.max(abs((linear @ z.ravel() + (ee @ current).T.ravel())[selection])))
    if alias_error > 1e-10:
        raise ValueError("非线性积分采样未收敛")
    coeff = _coeff(z, k)
    output = c.ov @ coeff
    points = [
        {
            "frequency_hz": float(index * base),
            "voltage_real_v": float(value.real),
            "voltage_imag_v": float(value.imag),
            "power_dbm": float(
                10
                * np.log10(max((1 if index == 0 else 2) * abs(value) ** 2 / c.output["value"] * 1000, 1e-300))
            ),
        }
        for index, value in enumerate(output)
    ]
    report = {
        "model": "intrinsic_ebers_moll_rc_hb_v1",
        "fundamental_hz": base,
        "harmonics": k,
        "samples": n,
        "output_resistor": c.output["id"],
        "points": points,
        "nodes": [
            {"node": name, "dc_v": float(z[j, 0]), "peak_v": float(np.max(abs(time[:, j])))}
            for j, name in enumerate(c.nodes)
        ],
        "solver": {
            "status": "converged",
            "residual_a": history[-1],
            "quadrature_residual_a": alias_error,
            "iterations": len(history) - 1,
            "dc_iterations": len(dc_history) - 1,
        },
        "scope": "RC network; intrinsic reciprocal Ebers-Moll NPN; constant capacitances; no Early, high-injection, transit-time, breakdown or self-heating model",
    }
    return report, c, z, time


def harmonic_balance(spec, fundamental_hz, harmonics=8):
    return _hb(spec, fundamental_hz, harmonics)[0]


def harmonic_convergence(spec, fundamental_hz, harmonics=8):
    if type(harmonics) is not int or not 1 <= harmonics <= 16:
        raise ValueError("电路加倍收敛检查需要1～16阶")
    first = harmonic_balance(spec, fundamental_hz, harmonics)
    second = harmonic_balance(spec, fundamental_hz, 2 * harmonics)
    # Voltage criterion includes phase, small components and newly resolved band.
    a = np.array([complex(p["voltage_real_v"], p["voltage_imag_v"]) for p in first["points"]])
    bb = np.array([complex(p["voltage_real_v"], p["voltage_imag_v"]) for p in second["points"]])
    aa = np.pad(a, (0, len(bb) - len(a)))
    limit = max(1e-9, float(np.max(abs(bb[1:]))) * 1e-4)
    error = float(np.max(abs(bb - aa)))
    second["convergence"] = {
        "status": "pass" if error <= limit else "fail",
        "harmonic_limits": [harmonics, 2 * harmonics],
        "max_voltage_delta_v": error,
        "limit_v": limit,
    }
    return second


def _stability(c, z, base, k):
    """Floquet test, eliminating algebraic nodes of an index-one RC system."""
    cap = c.cap[np.ix_(c.unknown, c.unknown)]
    eig, vectors = np.linalg.eigh(cap)
    active = eig > max(float(np.max(eig)) * 1e-12, 1e-24)
    rank = int(np.sum(active))

    def reduced(phase):
        angles = 2 * np.pi * phase * np.arange(1, k + 1)
        v = z @ np.r_[1, 2 * np.cos(angles), -2 * np.sin(angles)]
        _, jac = c.device(v)
        g = (c.g + jac[0])[np.ix_(c.unknown, c.unknown)]
        gg = vectors.T @ g @ vectors
        gd = gg[np.ix_(active, active)]
        if rank < c.nu:
            gaa = gg[np.ix_(~active, ~active)]
            if np.linalg.cond(gaa) > 1e10:
                raise ValueError("电路代数约束接近奇异点")
            gd = gd - gg[np.ix_(active, ~active)] @ linear_solve(gaa, gg[np.ix_(~active, active)])
        return -gd / eig[active, None] / base

    if rank == 0:
        for phase in np.linspace(0, 1, 4 * k + 1):
            reduced(phase)
        return {"status": "algebraic", "spectral_radius": 0.0}
    solution = solve_ivp(
        lambda t, x: (reduced(t) @ x.reshape(rank, rank)).ravel(),
        (0, 1),
        np.eye(rank).ravel(),
        method="BDF",
        rtol=1e-8,
        atol=1e-11,
        max_step=1 / (16 * k),
    )
    if not solution.success:
        raise ValueError("周期稳定性积分失败")
    radius = float(np.max(abs(np.linalg.eigvals(solution.y[:, -1].reshape(rank, rank)))))
    if not np.isfinite(radius) or radius >= 1 - 1e-6:
        raise ValueError("周期解不稳定或接近临界；不能计算稳态噪声")
    return {"status": "stable", "spectral_radius": radius}


def _noise_sources(c, time):
    sources = []
    for e, vector in c.resistors:
        temperature = e.get("temperature_k", c.spec["temperature_k"])
        sources.append((e["id"], vector[c.unknown], np.full(len(time), 2 * KB * temperature / e["value"])))
    for e, m, be, bc, ce in c.q:
        # The independent base/collector white-shot model is forward-active only.
        if np.min(time @ be) < 5 * c.vt or np.max(time @ bc) > -5 * c.vt:
            raise ValueError(e["id"] + "：周期噪声需要全周期保持正向放大区")
        forward = m["is_a"] * np.expm1(time @ be / c.vt)
        sources.extend(
            [
                (e["id"] + ":base", be[c.unknown], QE * forward / m["bf"]),
                (e["id"] + ":collector", ce[c.unknown], QE * forward),
            ]
        )
    return sources


def periodic_noise(spec, fundamental_hz, harmonics=8, offset_hz=10000.0, sidebands=8, output_harmonics=1):
    """Time-average one-sided voltage / resistor power PSD at positive sidebands."""
    base = number(fundamental_hz, "基频", 1, 1e11)
    offset = number(offset_hz, "噪声偏移", base * 1e-9, base * 0.499999)
    if type(sidebands) is not int or not 1 <= sidebands <= 32:
        raise ValueError("噪声边带数应为1～32")
    if type(output_harmonics) is not int or not 0 <= output_harmonics <= sidebands:
        raise ValueError("观测阶数不能超过噪声边带数")
    hb, c, z, _ = _hb(spec, base, harmonics)
    # Noise uses its own quadrature: Toeplitz differences extend to twice M.
    n = 16 * (2 * max(harmonics, sidebands) + 1)
    basis, _ = _basis(harmonics, n)
    time = basis @ z.T
    c.check_limits(time)
    stability = _stability(c, z, base, harmonics)
    sources = _noise_sources(c, time)
    _, jac = c.device(time)
    gf = np.fft.fft(jac[:, c.unknown][:, :, c.unknown], axis=0) / n
    indices = np.arange(-sidebands, sidebands + 1)
    count = len(indices)
    diff = (indices[:, None] - indices[None, :]) % n
    matrix = gf[diff].transpose(0, 2, 1, 3).reshape(count * c.nu, count * c.nu)
    guu, cuu = c.g[np.ix_(c.unknown, c.unknown)], c.cap[np.ix_(c.unknown, c.unknown)]
    for j, index in enumerate(indices):
        matrix[j * c.nu : (j + 1) * c.nu, j * c.nu : (j + 1) * c.nu] += (
            guu + 2j * np.pi * (offset + index * base) * cuu
        )
    condition = float(np.linalg.cond(matrix))
    if condition > 1e12:
        raise ValueError("周期噪声转换矩阵接近奇异点")
    # Output difference against an ideal source is noise-ground referenced.
    output = np.kron(np.eye(count), c.ov[c.unknown][None, :])
    transfer = linear_solve(matrix.T, output.T).T
    contributions = []
    total = np.zeros(count)
    max_correlation = 0.0
    for name, vector, density in sources:
        spectral = np.fft.fft(density) / n
        covariance = spectral[diff]
        # Exact Toeplitz of instantaneous PSD retains cyclostationary correlation;
        # multiplying by PSD itself twice would incorrectly square the intensity.
        h = transfer @ np.kron(np.eye(count), vector[:, None])
        psd = 2 * np.einsum("ij,jk,ik->i", h, covariance, h.conj()).real
        if np.min(psd) < -max(float(np.max(abs(psd))) * 1e-9, 1e-35):
            raise ValueError("噪声协方差失去半正定性")
        psd = np.maximum(psd, 0)
        total += psd
        contributions.append((name, psd))
        if spectral[0].real > 0:
            max_correlation = max(
                max_correlation, float(np.max(abs(spectral[1 : 2 * sidebands + 1]))) / spectral[0].real
            )
    points = []
    for j, index in enumerate(indices):
        if abs(index) > output_harmonics:
            continue
        frequency = abs(offset + index * base)
        density = float(total[j])
        points.append(
            {
                "frequency_hz": float(frequency),
                "voltage_noise_v2_hz": density,
                "noise_dbm_hz": float(10 * np.log10(max(density / c.output["value"] * 1000, 1e-300))),
                "contributions": [
                    {"source": name, "voltage_noise_v2_hz": float(p[j])} for name, p in contributions
                ],
            }
        )
    return {
        "model": "periodic_linearized_white_noise_v1",
        "points": points,
        "operating_point": hb,
        "fundamental_hz": base,
        "harmonics": harmonics,
        "sidebands": sidebands,
        "output_harmonics": output_harmonics,
        "offset_hz": offset,
        "stability": stability,
        "matrix_condition": condition,
        "max_source_cyclic_ratio": max_correlation,
        "scope": "one-sided time-average PSD; both positive and negative mixing sidebands; resistor thermal noise and independent forward-active NPN base/collector white shot noise; no flicker, transit-time noise correlation or oscillator phase noise",
    }


def noise_convergence(spec, fundamental_hz, harmonics=8, offset_hz=10000.0, sidebands=8, output_harmonics=1):
    if type(sidebands) is not int or not 1 <= sidebands <= 16:
        raise ValueError("噪声加倍收敛检查需要1～16条边带")
    # Refuse to certify a noise result around an unconverged harmonic truncation.
    hb = harmonic_convergence(spec, fundamental_hz, harmonics)
    if hb["convergence"]["status"] != "pass":
        raise ValueError("工作点谐波截断未收敛")
    first = periodic_noise(spec, fundamental_hz, 2 * harmonics, offset_hz, sidebands, output_harmonics)
    second = periodic_noise(spec, fundamental_hz, 2 * harmonics, offset_hz, 2 * sidebands, output_harmonics)
    refined = {p["frequency_hz"]: p["noise_dbm_hz"] for p in second["points"]}
    differences = [abs(a["noise_dbm_hz"] - refined[a["frequency_hz"]]) for a in first["points"]]
    second["convergence"] = {
        "status": "pass" if max(differences) <= 0.01 else "fail",
        "sideband_limits": [sidebands, 2 * sidebands],
        "max_delta_db": max(differences),
        "tolerance_db": 0.01,
        "harmonic": hb["convergence"],
    }
    return second


def spice_netlist(spec, fundamental_hz=None):
    """Export the exact deterministic model subset for independent SPICE checking."""
    validate(spec)
    lines = ["RF Link intrinsic Ebers-Moll RC reference", f".temp {spec['temperature_k'] - 273.15:.15g}"]
    for name, m in spec["models"].items():
        lines.append(
            f".model {name} NPN (IS={m['is_a']:.15g} BF={m['bf']:.15g} BR={m['br']:.15g} TNOM={spec['temperature_k'] - 273.15:.15g})"
        )
    for index, e in enumerate(spec["components"], 1):
        kind = e["type"]
        if kind in ("resistor", "capacitor"):
            lines.append(f"{'R' if kind == 'resistor' else 'C'}{index} {e['p']} {e['n']} {e['value']:.15g}")
            if kind == "resistor" and "temperature_k" in e:
                lines[-1] += f" TEMP={e['temperature_k'] - 273.15:.15g}"
        elif kind == "voltage":
            if e["tones"]:
                base = number(fundamental_hz, "基频", 1, 1e11)
                terms = [f"{e['dc_v']:.15g}"] + [
                    f"{t['peak_v']:.15g}*cos({2 * np.pi * base * t['harmonic']:.15g}*time+{np.deg2rad(t['phase_deg']):.15g})"
                    for t in e["tones"]
                ]
                lines.append(f"B{index} {e['p']} 0 V=" + "+".join(terms))
            else:
                lines.append(f"V{index} {e['p']} 0 {e['dc_v']:.15g}")
        else:
            m = spec["models"][e["model"]]
            lines.append(f"Q{index} {e['c']} {e['b']} {e['e']} {e['model']}")
            for suffix, node, cap in (("be", e["e"], m["cbe_f"]), ("bc", e["c"], m["cbc_f"])):
                if cap:
                    lines.append(f"C{index}{suffix} {e['b']} {node} {cap:.15g}")
    if any(e.get("tones") for e in spec["components"]):
        maximum = max(t["harmonic"] for e in spec["components"] for t in e.get("tones", []))
        step = 1 / (fundamental_hz * maximum * 512)
        lines.append(f".tran {step:.15g} {20 / fundamental_hz:.15g} {19 / fundamental_hz:.15g} {step:.15g}")
    else:
        lines.append(".op")
    lines += [".end", ""]
    return "\n".join(lines)
