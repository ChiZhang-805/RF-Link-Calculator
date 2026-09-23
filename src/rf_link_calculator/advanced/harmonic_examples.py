"""Explicit synthetic real-RF two-port models, separate from measured datasets."""

from dataclasses import replace

import numpy as np

from rf_link_calculator.domain.models import Analysis, Mixer, NoiseSpec, PowerSpec, Project, Stage, TwoTone

from .data import import_dataset


def examples():
    def block(sid, order, c, mixer=None, **meta):
        model = import_dataset(
            "harmonic",
            "order,coefficient\n" + "".join(f"{i},{value}\n" for i, value in enumerate(c, 1)),
            sid + "-polynomial.csv",
            {"source": "synthetic", "version": "1", "amplitude_unit": "sqrt_mw", "max_input": 2.0, **meta},
        )
        stage = Stage(
            id=sid,
            name={"feedback": "反馈放大器", "rf": "射频放大器", "mix": "混频器", "if": "中频放大器"}[sid],
            order=order + 1,
            type="mixer" if mixer else "amplifier",
            mixer=mixer,
            gain_db=float(20 * np.log10(c[0])),
            noise=NoiseSpec("unknown"),
            ip3=PowerSpec("unknown"),
            p1db=PowerSpec("unknown"),
            source={"kind": "synthetic", "note": "显式实射频多项式构造模型"},
        )
        return stage, {"harmonic": model}

    def build(name, blocks, settings):
        return Project(
            schema_version="2.0.0",
            project_id=name,
            project_name=name,
            link_name=name,
            analysis=Analysis(
                source_frequency_hz=10e6, input_power_dbm=-20, two_tone=TwoTone(True, -30, 2e6)
            ),
            stages=tuple(s for s, _ in blocks),
            notes="构造行为模型，未经商用软件或目标器件验收",
            lab={
                "models": {s.id: m for s, m in blocks},
                "settings": {"hb_fundamental_hz": 1e6, "hb_harmonics": 64, **settings},
            },
        )

    feedback = build(
        "谐波反馈链路",
        [block("feedback", 0, [2.0, 0.1, -0.2], s11=0.1, s12=0.2, s22=-0.1, pole_hz=20e6)],
        {"source_gamma": [0.3, 0], "load_gamma": [0.2, 0]},
    )
    mixer = build(
        "谐波混频链路",
        [
            block("rf", 0, [2.0, 0.1]),
            block("mix", 1, [0.5, 0.01], Mixer(lo_frequency_hz=7e6)),
            block("if", 2, [3.0, 0.2], pole_hz=5e6),
        ],
        {},
    )
    select = Stage(
        id="select",
        name="预选滤波器",
        order=1,
        type="filter_bpf",
        gain_db=-1,
        noise=NoiseSpec("passive_thermal"),
        ip3=PowerSpec("ideal"),
        p1db=PowerSpec("ideal"),
    )
    pre = Stage(
        id="lna",
        name="低噪声放大器",
        order=2,
        type="lna",
        gain_db=10,
        noise=NoiseSpec("manual", 2),
        ip3=PowerSpec("ideal"),
        p1db=PowerSpec("ideal"),
    )
    mix = Stage(
        id="down",
        name="下变频器",
        order=3,
        type="mixer",
        gain_db=-6,
        noise=NoiseSpec("manual", 4),
        mixer=Mixer(lo_frequency_hz=90e6, noise_convention="SSB", noise_compatible=True),
        ip3=PowerSpec("ideal"),
        p1db=PowerSpec("ideal"),
    )
    curve = import_dataset(
        "frequency",
        "frequency_hz,gain_db\n75000000,-45\n80000000,-40\n95000000,-1\n105000000,-1\n",
        "synthetic-preselector.csv",
        {"source": "synthetic"},
    )
    image = replace(
        feedback,
        project_id="镜像噪声链路",
        project_name="镜像噪声链路",
        link_name="镜像噪声链路",
        stages=(select, pre, mix),
        analysis=Analysis(source_frequency_hz=100e6),
        lab={"models": {"select": {"linear": curve}}, "settings": {"image_temperature_k": 290}},
    )
    return {p.link_name: p for p in (feedback, mixer, image)}
