"""Synthetic fixtures: no values in this module claim to be measured devices."""

from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

from .domain.models import Analysis, Mixer, NoiseSpec, PowerSpec, Project, Stage, TwoTone


def amp(name, gain, nf, ip3, p1, order=1):
    return Stage(
        name=name,
        order=order,
        gain_db=gain,
        noise=NoiseSpec("manual", nf),
        ip3=PowerSpec("finite", "input", ip3),
        p1db=PowerSpec("finite", "input", p1),
        compression_p=2,
        source={"kind": "synthetic_example", "note": "构造演示值，不代表实测或任何厂商"},
    )


def passive(name, gain, order=1, type="attenuator"):
    return Stage(
        name=name,
        order=order,
        type=type,
        gain_db=gain,
        noise=NoiseSpec("passive_thermal"),
        ip3=PowerSpec("ideal"),
        p1db=PowerSpec("ideal"),
        source={"kind": "synthetic_example", "note": "匹配热无源与理想线性是明确示例假设"},
    )


def examples() -> dict[str, Project]:
    a = Project(
        project_name="构造基准A",
        link_name="两级放大器",
        analysis=Analysis(input_power_dbm=-30, two_tone=TwoTone(True, -30, 1e5)),
        stages=(amp("放大器A", 10, 3, 10, 0), amp("放大器B", 10, 6, 20, 5, 2)),
    )
    b = Project(
        project_name="构造基准B",
        link_name="接收链路",
        analysis=Analysis(two_tone=TwoTone(True, -40, 1e5)),
        stages=(
            passive("输入滤波器", -2, type="filter_bpf"),
            replace(
                amp("低噪声放大器", 20, 1.5, 5, -9, 2),
                type="lna",
                ip3=PowerSpec("finite", "output", 25),
                p1db=PowerSpec("finite", "actual_output", 10),
            ),
            replace(amp("中频放大器", 10, 5, 10, 6, 3), p1db=PowerSpec("finite", "actual_output", 15)),
        ),
    )
    c = Project(
        project_name="构造基准C",
        link_name="累计压缩反例",
        analysis=Analysis(input_power_dbm=0),
        stages=(amp("零增益级1", 0, 0, 10, 0), amp("零增益级2", 0, 0, 10, 0, 2)),
    )
    d = Project(
        project_name="构造基准D",
        link_name="无源衰减器",
        analysis=Analysis(input_power_dbm=-30),
        stages=(passive("3 dB衰减器", -3),),
    )
    e = Project(
        project_name="构造基准E",
        link_name="源温度100K",
        analysis=Analysis(input_power_dbm=-50, source_noise_temperature_k=100),
        stages=(amp("10 dB放大器", 10, 3, 10, 0),),
    )
    f = Project(
        project_name="构造基准F",
        link_name="下变频链路",
        link_type="downconverter",
        analysis=Analysis(source_frequency_hz=2.4e9, signal_bandwidth_hz=1e7, noise_bandwidth_hz=1e7),
        stages=(
            passive("射频带通", -1, type="filter_bpf"),
            replace(amp("射频LNA", 20, 1.5, 5, -9, 2), type="lna"),
            Stage(
                order=3,
                name="下变频混频器",
                type="mixer",
                gain_db=-6,
                noise=NoiseSpec("manual", 8),
                mixer=Mixer(noise_convention="SSB", noise_compatible=True),
                source={"kind": "synthetic_example", "note": "有效SSB NF=8 dB为构造值，线性度未知"},
            ),
            replace(passive("中频带通", -2, 4, "filter_bpf"), frequency_range_hz=(50e6, 150e6)),
            amp("中频放大器", 20, 5, 10, 0, 5),
        ),
    )
    ideal = replace(
        d, link_name="全部理想线性", stages=(replace(d.stages[0], gain_db=0, noise=NoiseSpec("ideal")),)
    )
    long = replace(
        ideal,
        link_name="200级长链路",
        stages=tuple(replace(passive(f"器件{i:03d}", -0.01, i), id=f"long-{i}") for i in range(1, 201)),
    )
    missing = replace(
        a, link_name="缺NF但增益可算", stages=(replace(a.stages[0], noise=NoiseSpec()), a.stages[1])
    )
    result = {
        p.link_name: p
        for p in (
            a,
            b,
            c,
            d,
            e,
            replace(e, link_name="源温度600K", analysis=replace(e.analysis, source_noise_temperature_k=600)),
            f,
            missing,
            replace(a, link_name="缺P1", stages=(replace(a.stages[0], p1db=PowerSpec()), a.stages[1])),
            ideal,
            replace(a, link_name="全部禁用", stages=tuple(replace(s, enabled=False) for s in a.stages)),
            replace(
                a,
                link_name="强压缩",
                analysis=replace(a.analysis, input_power_dbm=10, two_tone=TwoTone(True, 10, 1e5)),
            ),
            replace(
                f,
                link_name="混频噪声口径未知",
                stages=tuple(replace(s, mixer=Mixer()) if s.type == "mixer" else s for s in f.stages),
            ),
            long,
            replace(
                a,
                link_name="中文与特殊字符",
                stages=(replace(a.stages[0], name='=中文超长名称 & <滤波> "测试" ' * 8), a.stages[1]),
            ),
        )
    }
    # Stable fixtures make the UI, checked-in JSON and generated exports share exact hashes.
    return {
        name: replace(
            p,
            project_id=str(uuid5(NAMESPACE_URL, "rf-link-example/" + name)),
            stages=tuple(
                replace(s, id=str(uuid5(NAMESPACE_URL, f"rf-link-example/{name}/{i}")))
                for i, s in enumerate(p.stages)
            ),
        )
        for name, p in result.items()
    }
