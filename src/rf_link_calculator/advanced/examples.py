"""Deterministic synthetic fixtures. They establish reproducibility, not device accuracy."""

from dataclasses import replace
from hashlib import sha256

import numpy as np

from rf_link_calculator.domain.models import Analysis, Mixer, NoiseSpec, PowerSpec, Project, Stage, TwoTone

from .data import import_dataset
from .nonlinear import basis, fit_memory


def examples():
    def amp(sid, **kwargs):
        return Stage(
            id=sid,
            name="放大器",
            gain_db=20,
            noise=NoiseSpec("manual", 2),
            ip3=PowerSpec("finite", "input", 10),
            p1db=PowerSpec("finite", "input", -10),
            source={"kind": "synthetic", "note": "构造模型，仅用于软件验证"},
            **kwargs,
        )

    def project(name, stages, models, analysis=None, settings=None, **extra):
        return Project(
            schema_version="2.0.0",
            project_id="fixture-" + name,
            project_name=name,
            link_name=name,
            stages=tuple(stages),
            analysis=analysis or Analysis(two_tone=TwoTone(True, -40, 1e5)),
            notes="合成数据，不代表真实器件或商业软件对标结果",
            lab={"models": models, "settings": settings or {}, **extra},
        )

    a = amp("frequency-amplifier")
    curve = import_dataset(
        "frequency",
        "frequency_hz,gain_db,nf_db,phase_deg,iip3_dbm,ip1_dbm\n80000000,18,2.5,30,8,-12\n90000000,19.5,2.1,15,9,-11\n100000000,20,2,0,10,-10\n110000000,19.5,2.1,-15,9,-11\n120000000,18,2.5,-30,8,-12\n",
        "synthetic-frequency.csv",
        {"source": "synthetic"},
    )
    freq = project(
        "扫频链路", [a], {a.id: {"linear": curve}}, settings={"sweep_low_hz": 80e6, "sweep_high_hz": 120e6}
    )
    b = amp("curve-amplifier")
    ampm = import_dataset(
        "ampm",
        "pin_dbm,pout_dbm,phase_deg\n-80,-60,0\n-40,-20,0\n-30,-10,0\n-20,0,0\n-15,4.7,1\n-10,9,3\n-5,12.5,6\n0,14,12\n",
        "synthetic-ampm.csv",
        {"source": "synthetic"},
    )
    nonlinear = project(
        "功率曲线链路",
        [b],
        {b.id: {"nonlinear": ampm}},
        Analysis(input_power_dbm=-10, two_tone=TwoTone(True, -20, 1e5)),
    )
    c = amp("sparameter-amplifier")
    touchstone = "# MHz S RI R 50\n80 0.1 0.1 8 1 0.01 0 -0.1 0\n100 0.1 0.05 10 0 0.01 0 -0.1 0\n120 0.1 0 8 -1 0.01 0 -0.1 0\n80 1.5 0.15 20 0.2\n100 1.5 0.15 20 0.2\n120 1.5 0.15 20 0.2\n"
    network = import_dataset("sparameter", touchstone, "synthetic-amplifier.s2p", {"source": "synthetic"})
    mismatch = project(
        "失配链路",
        [c],
        {c.id: {"linear": network}},
        settings={
            "source_gamma": [0.2, 0.1],
            "load_gamma": [-0.1, 0.05],
            "sweep_low_hz": 80e6,
            "sweep_high_hz": 120e6,
        },
    )
    m = Stage(
        id="imt-mixer",
        name="混频器",
        type="mixer",
        gain_db=-10,
        noise=NoiseSpec("manual", 6),
        mixer=Mixer(lo_frequency_hz=2.3e9, lo_power_dbm=10, noise_convention="SSB", noise_compatible=True),
        ip3=PowerSpec("finite", "input", 20),
        p1db=PowerSpec("finite", "input", 5),
    )
    imt = import_dataset(
        "imt",
        "m,n,output_dbm\n1,-1,-30\n2,-1,-70\n1,-2,-80\n0,1,-60\n",
        "synthetic-imt.csv",
        {"rf_hz": 2.4e9, "rf_dbm": -20, "lo_hz": 2.3e9, "lo_dbm": 10, "source": "synthetic"},
    )
    mixer = project(
        "混频杂散链路",
        [m],
        {m.id: {"mixer": imt}},
        Analysis(source_frequency_hz=2.4e9, input_power_dbm=-20),
        settings={"image_gain_db": -10, "image_temperature_k": 290},
    )
    rng = np.random.default_rng(27182)
    # Band-limited, persistently exciting waveform and a reproducible memory effect.
    n = 2048
    spec = np.zeros(n, complex)
    spec[:95] = rng.normal(size=95) + 1j * rng.normal(size=95)
    spec[-95:] = rng.normal(size=95) + 1j * rng.normal(size=95)
    x = np.fft.ifft(spec) * 3
    x[n // 2 :] *= 0.7
    y = basis(x, [1, 3], 2) @ np.array([10, 0.2j, -8 + 1j, 0.4])
    iq = {
        "kind": "iq",
        "i_in": x.real.tolist(),
        "q_in": x.imag.tolist(),
        "i_out": y.real.tolist(),
        "q_out": y.imag.tolist(),
        "metadata": {
            "source": "synthetic",
            "filename": "synthetic-iq.csv",
            "sha256": sha256(
                (
                    "i_in,q_in,i_out,q_out\n"
                    + "".join(
                        ",".join(repr(float(v)) for v in row) + "\n"
                        for row in zip(x.real, x.imag, y.real, y.imag)
                    )
                ).encode("utf-8")
            ).hexdigest(),
        },
    }
    model = fit_memory(iq, 10e6, 3, 2)
    w = amp("memory-amplifier")
    waveform = project(
        "宽带记忆链路",
        [w],
        {w.id: {"nonlinear": model}},
        settings={"sample_rate_hz": 10e6, "channel_bandwidth_hz": 1e6, "channel_spacing_hz": 2e6},
        iq=iq,
    )
    waveform = replace(waveform, analysis=replace(waveform.analysis, input_power_dbm=-40))
    return {p.link_name: p for p in (freq, nonlinear, mismatch, mixer, waveform)}
