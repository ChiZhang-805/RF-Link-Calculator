"""Complex-envelope behavioral models; samples have magnitude sqrt(mW)."""

import numpy as np
from scipy.optimize import brentq

from rf_link_calculator.engine.compression import compression_loss, input_reference

from .data import finite


def small_signal(model):
    if model["kind"] == "ampm":
        return 10 ** ((model["pout_dbm"][0] - model["pin_dbm"][0]) / 20) * np.exp(
            1j * np.deg2rad(model["phase_deg"][0])
        )
    c = np.asarray(model["coefficients"])
    c = c[:, 0] + 1j * c[:, 1]
    i = model["orders"].index(1)
    return np.sum(c[i * model["depth"] : (i + 1) * model["depth"]]) / model["scale"]


def basis(x, orders, depth):
    return np.column_stack(
        [
            np.r_[np.zeros(m, complex), (x * abs(x) ** (k - 1))[: len(x) - m]]
            for k in orders
            for m in range(depth)
        ]
    )


def model_p1(model):
    if model["kind"] == "ampm":
        gain = model["pout_dbm"][0] - model["pin_dbm"][0]
        loss = gain - (np.asarray(model["pout_dbm"]) - model["pin_dbm"])
        for i in range(1, len(loss)):
            if loss[i - 1] <= 1 <= loss[i]:
                return float(np.interp(1, loss[i - 1 : i + 1], model["pin_dbm"][i - 1 : i + 1]))
        return None
    gain = abs(small_signal(model))
    if gain < 1e-15:
        return None
    amplitudes = np.linspace(1e-6, model["max_input"], 301)
    coefficients = np.asarray(model["coefficients"])
    coefficients = (
        (coefficients[:, 0] + 1j * coefficients[:, 1])
        .reshape(len(model["orders"]), model["depth"])
        .sum(axis=1)
    )

    def loss(amplitude):
        out = sum(c * (amplitude / model["scale"]) ** k for c, k in zip(coefficients, model["orders"]))
        return 20 * np.log10(max(amplitude * gain / max(abs(out), 1e-300), 1e-300)) - 1

    for lo, hi in zip(amplitudes[:-1], amplitudes[1:]):
        if loss(lo) <= 0 <= loss(hi):
            return float(20 * np.log10(brentq(loss, lo, hi)))
    return None


def apply_model(x, model, sample_rate=None):
    x = np.asarray(x, complex)
    if model["kind"] == "ampm":
        pin = 10 * np.log10(np.maximum(abs(x) ** 2, 1e-300))
        if np.max(pin) > model["pin_dbm"][-1] + 1e-8:
            raise ValueError("瞬时输入功率超出AM/AM测量范围")
        gain = np.asarray(model["pout_dbm"]) - np.asarray(model["pin_dbm"])
        # Below the smallest measured level, continue its complex small-signal gain.
        g = np.interp(pin, model["pin_dbm"], gain)
        phase = np.interp(pin, model["pin_dbm"], np.unwrap(np.deg2rad(model["phase_deg"])))
        return x * 10 ** (g / 20) * np.exp(1j * phase)
    if sample_rate is not None and not np.isclose(sample_rate, model["sample_rate_hz"], rtol=1e-9):
        raise ValueError("IQ采样率与记忆模型不一致")
    if abs(x).max() > model["max_input"] * (1 + 1e-8):
        raise ValueError("输入峰值超出记忆模型训练范围")
    co = np.asarray(model["coefficients"])
    c = co[:, 0] + 1j * co[:, 1]
    return basis(x / model["scale"], model["orders"], model["depth"]) @ c


def cw_chain(project, pin):
    models = (project.lab or {}).get("models", {})
    # A memory model's zero-frequency steady-state is evaluated after filling delays.
    x = np.full(16, 10 ** (pin / 20), complex)
    rows = []
    for stage in project.stages:
        before = float(10 * np.log10(max(abs(x[-1]) ** 2, 1e-300)))
        if stage.enabled:
            model = models.get(stage.id, {}).get("nonlinear")
            if model:
                x = apply_model(x, model)
            else:
                if stage.gain_db is None or stage.p1db.mode == "unknown":
                    raise ValueError(f"{stage.name}：压缩参数不完整")
                c = (
                    0
                    if stage.p1db.mode == "ideal"
                    else compression_loss(
                        before,
                        input_reference(stage),
                        stage.compression_p or project.analysis.default_compression_p,
                    )
                )
                x *= 10 ** ((stage.gain_db - c) / 20)
        out = float(10 * np.log10(max(abs(x[-1]) ** 2, 1e-300)))
        rows.append({"stage_id": stage.id, "input_dbm": before, "output_dbm": out})
    return rows[-1]["output_dbm"] if rows else pin, rows


def p1_chain(project, gain):
    last = None
    for pin in np.linspace(-180, 100, 561):
        try:
            loss = pin + gain - cw_chain(project, float(pin))[0] - 1
        except ValueError:
            break
        if last and last[1] <= 0 <= loss:
            return float(brentq(lambda p: p + gain - cw_chain(project, p)[0] - 1, last[0], pin))
        last = (pin, loss)
    return None


def iq_arrays(data):
    return np.asarray(data["i_in"]) + 1j * np.asarray(data["q_in"]), np.asarray(
        data["i_out"]
    ) + 1j * np.asarray(data["q_out"])


def evm(reference, actual):
    den = np.vdot(reference, reference).real
    if den <= 0:
        raise ValueError("参考IQ功率为零")
    gain = np.vdot(reference, actual) / den
    error = actual - reference
    aligned = actual - gain * reference
    return {
        "nmse_db": float(10 * np.log10(max(float(np.vdot(error, error).real / den), 1e-30))),
        "evm_percent": float(100 * np.sqrt(np.vdot(error, error).real / den)),
        "evm_gain_aligned_percent": float(
            100 * np.sqrt(np.vdot(aligned, aligned).real / max(abs(gain) ** 2 * den, 1e-300))
        ),
        "gain_real": float(gain.real),
        "gain_imag": float(gain.imag),
    }


def fit_memory(data, sample_rate, order=5, depth=3, delay=0, ridge=0.0, validation_data=None):
    from .data import validate_dataset

    validate_dataset(data)
    if data["kind"] != "iq":
        raise ValueError("训练数据必须为IQ")
    sample_rate, ridge = finite(sample_rate), finite(ridge)
    if (
        sample_rate <= 0
        or order not in (1, 3, 5, 7, 9)
        or not 1 <= depth <= 8
        or not 0 <= delay <= 1024
        or not 0 <= ridge <= 1
    ):
        raise ValueError("记忆模型参数超出范围")
    x, y = iq_arrays(data)
    if delay:
        x, y = x[:-delay], y[delay:]
    orders = list(range(1, order + 1, 2))
    cut = len(x) if validation_data is not None else len(x) // 2
    gap = max(16, depth)
    if cut < 10 * len(orders) * depth + gap:
        raise ValueError("IQ样本不足以独立训练与验证")
    scale = float(np.sqrt(np.mean(abs(x[:cut]) ** 2)))
    if scale <= 0:
        raise ValueError("训练IQ输入功率为零")
    design = basis(x / scale, orders, depth)
    train = design[depth - 1 : cut]
    target = y[depth - 1 : cut]
    singular = np.linalg.svd(train, compute_uv=False)
    condition = float(singular[0] / max(singular[-1], 1e-300))
    if ridge == 0 and (np.linalg.matrix_rank(train) < train.shape[1] or condition > 1e12):
        raise ValueError("训练数据秩不足，无法识别所选记忆模型")
    if ridge:
        penalty = np.sqrt(ridge * len(train)) * np.eye(train.shape[1])
        train, target = np.vstack([train, penalty]), np.r_[target, np.zeros(train.shape[1])]
    co = np.linalg.lstsq(train, target, rcond=None)[0]
    predicted = design @ co
    if validation_data is None:
        validation_start = cut + gap
        validation_target, validation_prediction = y[validation_start:], predicted[validation_start:]
    else:
        validate_dataset(validation_data)
        if validation_data["kind"] != "iq":
            raise ValueError("验证数据必须为IQ")
        if validation_data.get("metadata", {}).get("amplitude_unit", "sqrt_mw") != data.get(
            "metadata", {}
        ).get("amplitude_unit", "sqrt_mw"):
            raise ValueError("训练和验证的幅度单位必须一致")
        vx, vy = iq_arrays(validation_data)
        if delay:
            vx, vy = vx[:-delay], vy[delay:]
        if len(vx) < max(32, depth * 10):
            raise ValueError("独立验证IQ样本不足")
        if abs(vx).max() > abs(x).max() * (1 + 1e-8):
            raise ValueError("独立验证输入峰值超出训练范围")
        validation_start = depth - 1
        validation_target = vy[validation_start:]
        validation_prediction = (basis(vx / scale, orders, depth) @ co)[validation_start:]
    model = {
        "kind": "memory",
        "orders": orders,
        "depth": depth,
        "sample_rate_hz": sample_rate,
        "scale": scale,
        "max_input": float(abs(x[:cut]).max()),
        "coefficients": np.column_stack([co.real, co.imag]).tolist(),
        "metadata": {
            "dataset_sha256": data.get("metadata", {}).get("sha256"),
            "amplitude_unit": data.get("metadata", {}).get("amplitude_unit", "sqrt_mw"),
            "validation_mode": "external_record" if validation_data is not None else "separated_half",
            "validation_sha256": (validation_data or {}).get("metadata", {}).get("sha256"),
            "delay_samples": delay,
            "ridge": ridge,
            "condition_number": condition,
            "train_samples": cut - depth + 1,
            "validation_start": validation_start,
            "validation_samples": len(validation_target),
            "training": evm(y[depth - 1 : cut], predicted[depth - 1 : cut]),
            "validation": evm(validation_target, validation_prediction),
        },
    }
    return model


def spectrum(x, sample_rate):
    # Hann PSD with power normalization; integral equals window-weighted mean power.
    window = np.hanning(len(x))
    power = abs(np.fft.fftshift(np.fft.fft(x * window))) ** 2 / (len(x) * np.sum(window**2))
    frequency = np.fft.fftshift(np.fft.fftfreq(len(x), 1 / sample_rate))
    return frequency, power


def wave_metrics(x, y, sample_rate, bandwidth, spacing):
    if not 0 < bandwidth <= spacing or spacing + bandwidth / 2 >= sample_rate / 2:
        raise ValueError("主信道/邻道必须分离并位于奈奎斯特范围内")
    frequency, power = spectrum(y, sample_rate)

    def band(center):
        mask = (frequency >= center - bandwidth / 2) & (frequency < center + bandwidth / 2)
        if np.sum(mask) < 3:
            raise ValueError("信道频率分辨率不足")
        return float(np.sum(power[mask]))

    main, left, right = band(0), band(-spacing), band(spacing)
    if main <= 0:
        raise ValueError("主信道功率为零")
    metrics = {
        **evm(x, y),
        "input_dbm": float(10 * np.log10(np.mean(abs(x) ** 2))),
        "output_dbm": float(10 * np.log10(max(np.mean(abs(y) ** 2), 1e-300))),
        "channel_dbm": float(10 * np.log10(main)),
        "acpr_left_dbc": float(10 * np.log10(max(left / main, 1e-30))),
        "acpr_right_dbc": float(10 * np.log10(max(right / main, 1e-30))),
    }
    # Decimation affects only chart delivery, never the integrated metrics.
    step = max(1, len(frequency) // 1024)
    return {
        "metrics": metrics,
        "points": [
            {"frequency_hz": float(f), "power_dbm": float(10 * np.log10(max(p, 1e-30)))}
            for f, p in zip(frequency[::step], power[::step])
        ],
        "model": "complex_envelope_memory_polynomial_v2",
        "samples": len(x),
    }
