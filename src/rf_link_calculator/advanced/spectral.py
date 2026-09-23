"""Feed-forward complex-envelope spectrum and drive-specific mixer spur tables."""

import numpy as np

from rf_link_calculator.engine.compression import compression_loss, input_reference
from rf_link_calculator.engine.frequency import map_frequency

from .data import finite, sample
from .network import stage_at
from .nonlinear import apply_model, iq_arrays, wave_metrics


def require_matched(project):
    settings = (project.lab or {}).get("settings", {})
    if any(abs(complex(*settings.get(k, [0, 0]))) > 1e-12 for k in ("source_gamma", "load_gamma")):
        raise ValueError("波形非线性分析需要匹配的源与负载")
    models = (project.lab or {}).get("models", {})
    for stage in project.stages:
        if not stage.enabled:
            continue
        if models.get(stage.id, {}).get("harmonic"):
            raise ValueError("实射频多项式模型请使用谐波分析")
        ds = models.get(stage.id, {}).get("linear", {})
        if ds and models.get(stage.id, {}).get("nonlinear"):
            raise ValueError("同级频率模型和完整功率曲线不能重复叠加增益；请拆成独立级")
        if ds.get("kind") == "sparameter":
            s = np.asarray(ds["s_real"]) + 1j * np.asarray(ds["s_imag"])
            if np.max(abs(s[:, (0, 0, 1), (0, 1, 1)])) > 1e-7 or not np.allclose(ds["z0_ohm"], 50):
                raise ValueError("含反射/反向耦合的网络需要大信号端口模型，不能套用单向AM/AM或IQ模型")


def transfer(stage, spec, frequencies):
    ds = spec.get("linear")
    if not ds:
        if stage.gain_db is None:
            raise ValueError(f"{stage.name}：增益缺失")
        return np.full(len(frequencies), 10 ** (stage.gain_db / 20), complex)
    if ds["kind"] == "frequency":
        gain = sample(ds, frequencies, "gain_db")
        phase = sample(ds, frequencies, "phase_deg") if "phase_deg" in ds else 0
        return 10 ** (gain / 20) * np.exp(1j * np.deg2rad(phase))
    # require_matched has checked 50-ohm unilateral data before this fast path.
    sample(ds, frequencies, "frequency_hz")
    s = np.asarray(ds["s_real"]) + 1j * np.asarray(ds["s_imag"])
    return np.interp(frequencies, ds["frequency_hz"], s[:, 1, 0])


def chain_wave(project, x, sample_rate, carrier, scalar_model="ip3"):
    require_matched(project)
    if scalar_model not in ("ip3", "p1", "linear"):
        raise ValueError("未知标量非线性模型")
    models = (project.lab or {}).get("models", {})
    offsets = np.fft.fftfreq(len(x), 1 / sample_rate)
    y = np.asarray(x, complex).copy()
    f = carrier
    orientation = 1
    traces = []
    for stage in project.stages:
        if not stage.enabled:
            continue
        spec = models.get(stage.id, {})
        _, _, stage = stage_at(stage, spec, f)
        model = spec.get("nonlinear")
        peak = float(10 * np.log10(max(np.max(abs(y) ** 2), 1e-300)))
        if stage.absolute_max_input_dbm is not None and peak > stage.absolute_max_input_dbm:
            raise ValueError(f"{stage.name}：峰值超过最大输入额定值")
        h = transfer(stage, spec, f + offsets)
        if model:
            if "linear" in spec:
                raise ValueError(f"{stage.name}：波形分析不能重复叠加小信号增益与完整功率曲线；请拆成独立级")
            y = apply_model(y, model, sample_rate)
        elif scalar_model == "ip3" and stage.ip3.mode == "finite":
            p = input_reference(stage, "ip3")
            if peak > p - 10:
                raise ValueError(f"{stage.name}：超出三阶多项式弱非线性范围")
            y = y * (1 - abs(y) ** 2 / 10 ** (p / 10))
            y = np.fft.ifft(np.fft.fft(y) * h)
        elif scalar_model == "p1" and stage.p1db.mode == "finite":
            pins = 10 * np.log10(np.maximum(abs(y) ** 2, 1e-300))
            losses = np.array(
                [
                    compression_loss(
                        float(p),
                        input_reference(stage),
                        stage.compression_p or project.analysis.default_compression_p,
                    )
                    for p in pins
                ]
            )
            y *= 10 ** (-losses / 20)
            y = np.fft.ifft(np.fft.fft(y) * h)
        else:
            if (
                scalar_model != "linear"
                and getattr(stage, "ip3" if scalar_model == "ip3" else "p1db").mode == "unknown"
            ):
                raise ValueError(f"{stage.name}：非线性数据缺失")
            y = np.fft.ifft(np.fft.fft(y) * h)
        fout, inverted = map_frequency(stage, f)
        if fout is None or fout <= sample_rate / 2:
            raise ValueError(f"{stage.name}：输出分析带宽跨越零频")
        if inverted:
            y = np.conj(y)
            orientation *= -1
        f = fout
        power = abs(np.fft.fft(y) / len(y)) ** 2
        traces.append(
            {
                "stage_id": stage.id,
                "carrier_hz": f,
                "orientation": orientation,
                "power_dbm": (10 * np.log10(np.maximum(power, 1e-30))).tolist(),
            }
        )
    return y, f, traces


def tones(project, count=4096, scalar_model="ip3", custom=None, oversampling=1):
    if count not in (2048, 4096, 8192, 16384):
        raise ValueError("频谱点数无效")
    spacing = project.analysis.two_tone.spacing_hz
    if spacing <= 0:
        raise ValueError("双音间隔必须为正数")
    if oversampling not in (1, 2, 4, 8):
        raise ValueError("过采样倍率应为1、2、4或8")
    sample_rate = spacing * 32 * oversampling
    source = custom or [
        {
            "offset_hz": -spacing / 2,
            "power_dbm": project.analysis.two_tone.each_tone_power_dbm,
            "phase_deg": 0,
        },
        {
            "offset_hz": spacing / 2,
            "power_dbm": project.analysis.two_tone.each_tone_power_dbm,
            "phase_deg": 0,
        },
    ]
    if not isinstance(source, list) or not 1 <= len(source) <= 16:
        raise ValueError("载波数应为1～16")
    x = np.zeros(count, complex)
    bins = []
    for tone in source:
        offset, power, phase = (finite(tone.get(k, 0), k) for k in ("offset_hz", "power_dbm", "phase_deg"))
        k = offset / sample_rate * count
        if abs(k - round(k)) > 1e-7 or abs(offset) >= sample_rate / 2 or abs(power) > 600:
            raise ValueError("载波必须位于FFT频率栅格内，且功率在数值范围内")
        bins.append(round(k) % count)
        x += 10 ** (power / 20) * np.exp(1j * (2 * np.pi * k * np.arange(count) / count + np.deg2rad(phase)))
    if len(set(bins)) != len(bins):
        raise ValueError("载波频率重复")
    y, carrier, traces = chain_wave(
        project, x, sample_rate, project.analysis.source_frequency_hz, scalar_model
    )
    powers = abs(np.fft.fft(y) / count) ** 2
    offsets = np.fft.fftfreq(count, 1 / sample_rate)
    # Include fundamentals, IM3 and strongest products. Do not transfer full stage FFT arrays.
    selected = set(np.argsort(powers)[-48:].tolist()) | set(bins)
    for k in (-1.5, -0.5, 0.5, 1.5):
        selected.add(round(k * spacing / sample_rate * count) % count)
    rows = []
    for index in sorted(selected, key=lambda i: offsets[i]):
        p = float(10 * np.log10(max(powers[index], 1e-30)))
        if p < -250 and index not in bins:
            continue
        path = []
        orientation = traces[-1]["orientation"] if traces else 1
        for t in traces:
            sign = t["orientation"] * orientation
            source_index = (index * sign) % count
            path.append(
                {
                    "stage_id": t["stage_id"],
                    "frequency_hz": t["carrier_hz"] + float(offsets[source_index]),
                    "power_dbm": t["power_dbm"][source_index],
                }
            )
        rows.append(
            {
                "frequency_hz": float(carrier + offsets[index]),
                "offset_hz": float(offsets[index]),
                "power_dbm": p,
                "path": path,
            }
        )
    # A second FFT size is exposed to users and tests to verify convergence explicitly.
    return {
        "points": rows,
        "sample_rate_hz": sample_rate,
        "samples": count,
        "carrier_hz": carrier,
        "input_tones": source,
        "model": "feedforward_complex_envelope_fft_v2",
    }


def convergence(project, count=4096, scalar_model="ip3", custom=None, oversampling=1, tolerance_db=0.01):
    if oversampling not in (1, 2, 4) or not 0 < tolerance_db <= 0.1:
        raise ValueError("收敛检查倍率或容差无效")
    baseline = tones(project, count, scalar_model, custom, oversampling)
    refined = tones(project, count, scalar_model, custom, oversampling * 2)
    floor = max(p["power_dbm"] for p in baseline["points"]) - 100
    reference = {round(p["frequency_hz"], 3): p for p in refined["points"]}
    deltas = []
    for point in baseline["points"]:
        if point["power_dbm"] < floor:
            continue
        other = reference.get(round(point["frequency_hz"], 3))
        delta = None if other is None else other["power_dbm"] - point["power_dbm"]
        deltas.append(
            {
                "frequency_hz": point["frequency_hz"],
                "delta_db": delta,
                "status": "pass" if delta is not None and abs(delta) <= tolerance_db else "fail",
            }
        )
    report = {
        "status": "pass" if deltas and all(p["status"] == "pass" for p in deltas) else "fail",
        "tolerance_db": tolerance_db,
        "floor_dbm": floor,
        "sample_rates_hz": [baseline["sample_rate_hz"], refined["sample_rate_hz"]],
        "scope": "baseline detected products within 100 dB of its strongest component; no global HB claim",
        "components": deltas,
    }
    return {**refined, "convergence": report}


def spur_table(project):
    models = (project.lab or {}).get("models", {})
    components = [
        {
            "frequency_hz": project.analysis.source_frequency_hz,
            "power_dbm": project.analysis.input_power_dbm,
            "origin": "input",
            "product": "RF",
            "path": [],
        }
    ]
    for stage in project.stages:
        if not stage.enabled:
            continue
        spec = models.get(stage.id, {})
        out = []
        if stage.type == "mixer":
            model = spec.get("mixer")
            if not model or not stage.mixer:
                raise ValueError(f"{stage.name}：需要混频杂散表")
            meta = model["metadata"]
            # IMT is a measured single-RF + LO table. No invented multi-tone cross products.
            if len(components) != 1:
                raise ValueError("单载波IMT不适用于已有多个频谱分量的级联混频")
            source = components[0]
            expected = {
                "rf_hz": source["frequency_hz"],
                "rf_dbm": source["power_dbm"],
                "lo_hz": stage.mixer.lo_frequency_hz,
                "lo_dbm": stage.mixer.lo_power_dbm,
            }
            for k, value in expected.items():
                if value is None or not np.isclose(value, meta[k], rtol=1e-9, atol=1e-6):
                    raise ValueError(f"{stage.name}：{k}与IMT测试条件不一致")
            for m, n, power in zip(model["m"], model["n"], model["output_dbm"]):
                frequency = abs(m * source["frequency_hz"] + n * stage.mixer.lo_frequency_hz)
                if frequency <= 0:
                    continue
                out.append(
                    {
                        "frequency_hz": frequency,
                        "power_dbm": power,
                        "origin": stage.id,
                        "product": f"{m:g}RF{n:+g}LO",
                        "path": source["path"] + [{"stage_id": stage.id, "power_dbm": power}],
                    }
                )
        else:
            for component in components:
                block, _, _ = stage_at(stage, spec, component["frequency_hz"])
                if abs(block[0, 0]) + abs(block[0, 1]) + abs(block[1, 1]) > 1e-7:
                    raise ValueError("杂散逐级功率传播需要单向匹配端口")
                power = component["power_dbm"] + 20 * np.log10(max(abs(block[1, 0]), 1e-150))
                out.append(
                    {
                        **component,
                        "power_dbm": float(power),
                        "path": component["path"] + [{"stage_id": stage.id, "power_dbm": float(power)}],
                    }
                )
        components = out
    return {"points": components, "model": "measured_single_rf_lo_imt_v2"}


def image_noise(project, image_gain_db, image_temperature_k):
    from .network import KB, T0, evaluate

    temp, gain = finite(image_temperature_k), finite(image_gain_db)
    if temp < 0 or abs(gain) > 300:
        raise ValueError("镜像温度或增益无效")
    mixers = [s for s in project.stages if s.enabled and s.type == "mixer"]
    if len(mixers) != 1 or not mixers[0].mixer:
        raise ValueError("镜像分析需要恰好一个混频器")
    mixer = mixers[0]
    if mixer.mixer.noise_convention != "SSB" or not mixer.mixer.noise_compatible:
        raise ValueError("镜像分析需要确认SSB噪声口径")
    # SSB NF includes the matched 290 K image contribution. Replace it, never add it twice.
    values, _ = evaluate(project)
    if values["nf_db"] is None:
        raise ValueError("链路噪声数据不完整")
    ratio = 10 ** ((gain - values["gain_db"]) / 10)
    f = 10 ** (values["nf_db"] / 10)
    equivalent = T0 * (f - 1) + ratio * (temp - T0)
    if equivalent < -1e-6:
        raise ValueError("镜像增益与SSB噪声系数不一致")
    total = project.analysis.source_noise_temperature_k + max(0, equivalent)
    output = 10 * np.log10(KB * total * project.analysis.noise_bandwidth_hz * 1000) + values["gain_db"]
    return {
        "image_gain_db": gain,
        "image_temperature_k": temp,
        "equivalent_noise_temperature_k": max(0, equivalent),
        "noise_output_dbm": float(output),
        "model": "ssb_image_termination_replacement_v2",
    }


def waveform(project, sample_rate, bandwidth, spacing, mode="predict", scalar_model="ip3"):
    data = (project.lab or {}).get("iq")
    if not data:
        raise ValueError("请导入IQ数据")
    x, measured = iq_arrays(data)
    if mode == "measured":
        y = measured
    elif mode == "predict":
        y, _, _ = chain_wave(project, x, sample_rate, project.analysis.source_frequency_hz, scalar_model)
    else:
        raise ValueError("波形模式无效")
    # Exclude initial delay taps consistently; a captured IQ pair must already be aligned.
    depth = max(
        [1] + [v.get("nonlinear", {}).get("depth", 1) for v in (project.lab or {}).get("models", {}).values()]
    )
    result = wave_metrics(x[depth - 1 :], y[depth - 1 :], sample_rate, bandwidth, spacing)
    if mode == "predict":
        from .nonlinear import evm

        result["validation"] = evm(measured[depth - 1 :], y[depth - 1 :])
    result["mode"] = mode
    return result


def automatic_image_noise(project, image_temperature_k=290):
    """Single real downconverter, independent signal/image noise, matched ports.

    Mixer SSB NF already contains its 290 K image termination. Subtract that
    reference term before adding the actual filtered image source/noise branch.
    This is linear two-sideband noise propagation, not pumped nonlinear noise HB.
    """
    from .network import KB, T0

    settings = (project.lab or {}).get("settings", {})
    if any(abs(complex(*settings.get(key, [0, 0]))) > 1e-12 for key in ("source_gamma", "load_gamma")):
        raise ValueError("自动镜像噪声需要匹配终端")
    temp = finite(image_temperature_k)
    if temp < 0:
        raise ValueError("镜像温度不能为负数")
    stages = [s for s in project.stages if s.enabled]
    mixers = [i for i, s in enumerate(stages) if s.type == "mixer"]
    if len(mixers) != 1:
        raise ValueError("自动镜像噪声需要恰好一个下变频器")
    index = mixers[0]
    mixer = stages[index]
    m = mixer.mixer
    if not m or m.relation != "difference" or m.noise_convention != "SSB" or not m.noise_compatible:
        raise ValueError("需要确认SSB口径的差频混频器")
    signal = project.analysis.source_frequency_hz
    image = 2 * m.lo_frequency_hz - signal
    output = abs(signal - m.lo_frequency_hz)
    bandwidth = project.analysis.noise_bandwidth_hz
    if min(image, output) <= bandwidth / 2 or signal == image:
        raise ValueError("信号、镜像或输出频带跨越零频")
    models = (project.lab or {}).get("models", {})

    def block(stage, frequency):
        spec = models.get(stage.id, {})
        if spec.get("nonlinear") or spec.get("harmonic"):
            raise ValueError("自动镜像噪声不支持大信号非线性工作点")
        s, c, _ = stage_at(stage, spec, frequency)
        if abs(s[0, 0]) + abs(s[1, 1]) > 1e-8:
            raise ValueError("自动镜像噪声需要匹配器件端口")
        if c is None:
            raise ValueError(f"{stage.name}：噪声模型不完整")
        return float(abs(s[1, 0]) ** 2), float(c[1, 1].real)

    def prefix(frequency, temperature):
        gain, density = 1.0, KB * temperature
        for stage in stages[:index]:
            g, added = block(stage, frequency)
            density = density * g + added
            gain *= g
        return gain, density

    gs, ns = prefix(signal, project.analysis.source_noise_temperature_k)
    gi, ni = prefix(image, temp)
    ms, mixer_added = block(mixer, signal)
    mi, _ = block(mixer, image)
    intrinsic = mixer_added - KB * T0 * mi
    if intrinsic < -1e-10 * max(mixer_added, KB * T0 * mi):
        raise ValueError("SSB噪声系数低于镜像通道贡献，请核对噪声口径与转换增益")
    contributions = [ns * ms, ni * mi, max(0.0, intrinsic), 0.0]
    post = 1.0
    for stage in stages[index + 1 :]:
        g, added = block(stage, output)
        contributions = [p * g for p in contributions]
        contributions[-1] += added
        post *= g
    signal_gain, image_gain = gs * ms * post, gi * mi * post
    density = sum(contributions)
    te = density / (KB * signal_gain) - project.analysis.source_noise_temperature_k
    return {
        "model": "matched_two_sideband_noise_v1",
        "signal_frequency_hz": signal,
        "image_frequency_hz": image,
        "frequency_output_hz": output,
        "gain_db": float(10 * np.log10(signal_gain)),
        "image_gain_db": float(10 * np.log10(image_gain)),
        "image_temperature_k": temp,
        "equivalent_noise_temperature_k": max(0.0, te),
        "nf_db": float(10 * np.log10(1 + max(0.0, te) / T0)),
        "noise_output_dbm": float(10 * np.log10(max(density * bandwidth * 1000, 1e-300))),
        "contributions": [
            {"name": name, "density_w_hz": value}
            for name, value in zip(("信号通道", "镜像通道", "混频器内部", "后级"), contributions)
        ],
        "bandwidth_hz": bandwidth,
        "scope": "center-frequency small-signal matched two-sideband SSB budget; no nonlinear pumped-noise claim",
    }
