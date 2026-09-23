import math
import random
from dataclasses import replace

import pytest

from rf_link_calculator.domain.models import Mixer, NoiseSpec, PowerSpec, Project, TwoTone
from rf_link_calculator.engine import calculate
from rf_link_calculator.engine.compression import (
    closed_p1,
    compression_loss,
    input_reference,
    propagate,
    solve_system_p1db,
)
from rf_link_calculator.engine.units import dbm_to_mw, dbm_to_w, mw_to_dbm, w_to_dbm
from rf_link_calculator.examples import amp, examples, passive


def value(p, key):
    return calculate(p).metrics[key].value


@pytest.mark.parametrize("dbm", [-300, -100, -30, 0, 40, 300])
def test_units(dbm):
    assert mw_to_dbm(dbm_to_mw(dbm)) == pytest.approx(dbm, abs=1e-12)
    assert w_to_dbm(dbm_to_w(dbm)) == pytest.approx(dbm, abs=1e-12)


@pytest.mark.parametrize(
    "name,key,expected",
    [
        ("两级放大器", "gain_db", 20),
        ("两级放大器", "nf_db", 3.604740296957),
        ("两级放大器", "iip3_dbm", 6.98970004336),
        ("两级放大器", "ip1_dbm", -5.206963425791),
        ("两级放大器", "noise_output_dbm", -90.370446897),
        ("两级放大器", "im3_dbm", -83.97940008672),
        ("接收链路", "gain_db", 28),
        ("接收链路", "nf_db", 3.565977),
        ("接收链路", "iip3_dbm", -8.135209),
        ("接收链路", "ip1_dbm", -12.206963425791),
        ("接收链路", "im3_dbm", -75.729582),
        ("接收链路", "sensitivity_dbm", -100.409210),
        ("累计压缩反例", "ip1_dbm", -1.50514997832),
        ("累计压缩反例", "compressed_output_dbm", -1.682084899),
        ("无源衰减器", "nf_db", 3),
        ("无源衰减器", "noise_output_dbm", -113.975187),
        ("源温度100K", "noise_input_dbm", -112.703848),
        ("源温度600K", "noise_input_dbm", -109.111977),
        ("下变频链路", "gain_db", 31),
        ("下变频链路", "frequency_output_hz", 1e8),
    ],
)
def test_published_benchmarks(name, key, expected):
    assert value(examples()[name], key) == pytest.approx(expected, abs=1e-6)


def test_b_per_stage():
    r = calculate(examples()["接收链路"])
    assert [s.metrics["linear_output_dbm"].value for s in r.stages] == [-62, -42, -32]
    assert [s.metrics["nf_db"].value for s in r.stages] == pytest.approx([2, 3.5, 3.565977], abs=1e-6)


@pytest.mark.parametrize("p", [1, 2, 3, 10])
def test_compression_anchor_and_monotonicity(p):
    assert compression_loss(-10, -10, p) == pytest.approx(1)
    ys = [x - compression_loss(x, 0, p) for x in range(-80, 61)]
    assert all(y >= x - 1e-12 for x, y in zip(ys, ys[1:]))


def test_random_closed_form_independent():
    rng = random.Random(20260923)
    for _ in range(100):
        p = rng.uniform(1, 10)
        stages = tuple(
            replace(amp(f"s{i}", rng.uniform(-8, 25), 3, 10, rng.uniform(-20, 15), i + 1), compression_p=p)
            for i in range(rng.randint(1, 10))
        )
        assert solve_system_p1db(stages).value == pytest.approx(closed_p1(stages), abs=1e-7)


def test_mixed_p_and_reference():
    stages = (
        replace(amp("A", 10, 3, 10, 0), compression_p=1),
        replace(amp("B", 20, 3, 10, 3, 2), compression_p=10),
    )
    x = solve_system_p1db(stages).value
    assert x + 30 - propagate(stages, x)[0] == pytest.approx(1, abs=1e-7)
    assert closed_p1(stages) is None
    assert input_reference(replace(stages[0], p1db=PowerSpec("finite", "actual_output", 10))) == 1
    assert input_reference(replace(stages[0], p1db=PowerSpec("finite", "linear_output", 10))) == 0


def test_independent_missing_states():
    p = examples()["两级放大器"]
    for key, lost, kept in [
        ("noise", "nf_db", "iip3_dbm"),
        ("ip3", "iip3_dbm", "nf_db"),
        ("p1db", "ip1_dbm", "nf_db"),
    ]:
        s = replace(p.stages[0], **{key: NoiseSpec() if key == "noise" else PowerSpec()})
        r = calculate(replace(p, stages=(s, p.stages[1])))
        assert r.metrics[lost].status == "unknown"
        assert r.metrics[kept].value is not None
        assert r.metrics["gain_db"].value == 20
    assert calculate(examples()["全部理想线性"]).metrics["ip1_dbm"].status == "ideal"


def test_metamorphic():
    p = examples()["两级放大器"]
    base = calculate(p)
    bigger = calculate(replace(p, analysis=replace(p.analysis, noise_bandwidth_hz=1e7)))
    assert base.metrics["sfdr3_db"].value - bigger.metrics["sfdr3_db"].value == pytest.approx(20 / 3)
    raised = calculate(
        replace(p, analysis=replace(p.analysis, input_power_dbm=-29, two_tone=TwoTone(True, -29, 1e5)))
    )
    assert raised.metrics["im3_dbm"].value - base.metrics["im3_dbm"].value == pytest.approx(3)
    assert raised.metrics["linear_output_dbm"].value - base.metrics["linear_output_dbm"].value == 1
    renamed = calculate(replace(p, stages=tuple(replace(s, name="改名") for s in p.stages)))
    assert renamed.calculation_hash == base.calculation_hash
    assert renamed.project_hash != base.project_hash
    reordered = replace(p, stages=tuple(replace(s, order=i + 1) for i, s in enumerate(reversed(p.stages))))
    assert value(reordered, "gain_db") == 20
    assert value(reordered, "nf_db") != base.metrics["nf_db"].value
    disabled = replace(p, stages=(replace(p.stages[0], enabled=False), p.stages[1]))
    assert value(disabled, "nf_db") == value(replace(p, stages=(p.stages[1],)), "nf_db")


@pytest.mark.parametrize("count", [0, 1, 10, 50, 200])
def test_scale(count):
    p = Project(stages=tuple(replace(passive(str(i), -0.01, i + 1), id=f"s{i}") for i in range(count)))
    r = calculate(p)
    assert len(r.stages) == count
    assert r.metrics["gain_db"].value == pytest.approx(-count / 100)


def test_tone_has_own_compression_screen():
    p = examples()["两级放大器"]
    r = calculate(
        replace(p, analysis=replace(p.analysis, input_power_dbm=-80, two_tone=TwoTone(True, 20, 1e5)))
    )
    assert r.metrics["compression_db"].value < 1e-6
    assert r.metrics["im3_dbm"].status == "not_applicable"
    assert r.metrics["tone_total_dbm"].value == pytest.approx(23.0102999566)


def test_mixer_and_limits():
    p = examples()["下变频链路"]
    r = calculate(p)
    assert not any(i.code == "W004" for i in r.issues)
    assert r.stages[3].metrics["frequency_input_hz"].value == 1e8
    bad = calculate(examples()["混频噪声口径未知"])
    assert bad.metrics["nf_db"].value is None
    assert bad.metrics["gain_db"].value == 31
    s = replace(
        p.stages[2], mixer=Mixer(lo_frequency_hz=2.5e9, noise_convention="SSB", noise_compatible=True)
    )
    assert calculate(replace(p, stages=(s,))).stages[0].metrics["spectrum_inverted"].value == 1


def test_extreme_log_domain():
    # 200 x -300 dB is unrepresentable as a linear gain, but its dB budget remains usable.
    p = Project(stages=tuple(replace(passive(str(i), -300, i + 1), id=f"s{i}") for i in range(200)))
    r = calculate(p)
    assert r.metrics["gain_db"].value == -60000
    assert r.metrics["nf_db"].value == pytest.approx(60000)
    assert r.metrics["te_k"].status == "failed"
    assert math.isfinite(r.metrics["noise_output_dbm"].value)


def test_invalid_numeric_and_compression_failures(monkeypatch):
    from rf_link_calculator.engine.calculate import metric
    from rf_link_calculator.engine.units import db_to_ratio, ratio_to_db

    for number in (0, -1, float("inf")):
        with pytest.raises(ValueError):
            ratio_to_db(number)
    with pytest.raises(ValueError):
        db_to_ratio(-10000)
    with pytest.raises(ValueError):
        compression_loss(0, 0, 0)
    assert metric(float("inf")).status == "failed"
    s = amp("A", 10, 3, 10, 0)
    with pytest.raises(ValueError):
        propagate((replace(s, gain_db=None),), 0)
    with pytest.raises(ValueError):
        propagate((replace(s, p1db=PowerSpec()),), 0)
    assert propagate((replace(s, enabled=False),), -30)[0] == -30
    assert solve_system_p1db((replace(s, compression_p=20),)).status == "failed"
    assert closed_p1((replace(s, p1db=PowerSpec()),)) is None
    assert closed_p1((replace(s, p1db=PowerSpec("finite", "input", None)),)) is None
    import rf_link_calculator.engine.compression as module

    monkeypatch.setattr(module, "propagate", lambda stages, x, p: (x + 10, []))
    assert solve_system_p1db((s,)).status == "failed"


def test_ideal_insertion_and_passive_temperature():
    p = examples()["两级放大器"]
    middle = replace(passive("理想零增益", 0, 2), noise=NoiseSpec("ideal"))
    q = replace(p, stages=(p.stages[0], middle, replace(p.stages[1], order=3)))
    for key in ("nf_db", "iip3_dbm", "ip1_dbm"):
        assert value(p, key) == pytest.approx(value(q, key), abs=1e-7)
    assert closed_p1(q.stages) == pytest.approx(value(q, "ip1_dbm"), abs=1e-7)
    s = replace(passive("无源", -3), physical_temperature_k=580)
    # F=1+(L-1)*2, independently calculated.
    assert value(Project(stages=(s,)), "nf_db") == pytest.approx(10 * math.log10(1 + 2 * (10**0.3 - 1)))


def test_frequency_range_zero_crossing_and_sum():
    from rf_link_calculator.engine.frequency import map_frequency

    p = examples()["下变频链路"]
    mixer = p.stages[2]
    assert map_frequency(replace(mixer, mixer=None), 2.4e9)[0] is None
    assert map_frequency(replace(mixer, mixer=Mixer(relation="sum")), 2.4e9)[0] == 4.7e9
    r = calculate(replace(p, stages=(replace(mixer, mixer=None), p.stages[3])))
    assert r.metrics["frequency_output_hz"].value is None
    assert r.metrics["nf_db"].value is None
    zero = replace(mixer, mixer=Mixer(lo_frequency_hz=2.4e9, noise_convention="SSB", noise_compatible=True))
    assert calculate(replace(p, stages=(zero,))).metrics["frequency_output_hz"].value is None
    q = examples()["两级放大器"]
    narrow = replace(q.stages[0], frequency_range_hz=(99.99e6, 100.01e6))
    r = calculate(replace(q, stages=(narrow, q.stages[1])))
    assert r.metrics["im3_dbm"].status == "not_applicable"
    assert any(i.code == "W004" for i in r.issues)
    crossing = replace(
        mixer, mixer=Mixer(lo_frequency_hz=2.4e9 + 0.6e6, noise_convention="SSB", noise_compatible=True)
    )
    conditions = replace(p.analysis, signal_bandwidth_hz=1e5, two_tone=TwoTone(True, -50, 1e6))
    assert (
        calculate(replace(p, analysis=conditions, stages=(crossing,))).metrics["im3_dbm"].status
        == "not_applicable"
    )


def test_gain_missing_ratings_and_negative_dynamic_range():
    p = examples()["两级放大器"]
    missing = replace(p.stages[0], gain_db=None)
    r = calculate(replace(p, stages=(missing, p.stages[1])))
    assert r.metrics["gain_db"].value is None
    assert any(i.code == "E002" for i in r.issues)
    r = calculate(
        replace(
            p,
            analysis=replace(p.analysis, required_snr_db=200),
            stages=(replace(p.stages[0], absolute_max_input_dbm=-40), p.stages[1]),
        )
    )
    assert r.metrics["compression_dr_db"].value < 0
    assert {"E007", "W014"}.issubset({i.code for i in r.issues})
