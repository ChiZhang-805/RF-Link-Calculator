import json
from dataclasses import replace

import numpy as np
import pytest
from scipy.integrate import solve_ivp
from scipy.optimize import brentq

from rf_link_calculator.advanced import harmonic
from rf_link_calculator.advanced.api import dispatch
from rf_link_calculator.advanced.data import import_dataset
from rf_link_calculator.advanced.spectral import automatic_image_noise, tones
from rf_link_calculator.domain.codec import project_from_dict
from rf_link_calculator.domain.models import Analysis, Mixer, NoiseSpec, PowerSpec, Project, Stage
from rf_link_calculator.persistence.json_io import dumps, loads


def hb_project(coefficients=(2.0, 0.1, -0.2), meta=None, mixer=None, settings=None):
    stage = Stage(
        id="a",
        name="A",
        type="mixer" if mixer else "amplifier",
        mixer=mixer,
        gain_db=20 * np.log10(coefficients[0]),
        noise=NoiseSpec("ideal"),
        ip3=PowerSpec("unknown"),
        p1db=PowerSpec("unknown"),
    )
    model = import_dataset(
        "harmonic",
        "order,coefficient\n" + "".join(f"{i},{c}\n" for i, c in enumerate(coefficients, 1)),
        "polynomial.csv",
        {"source": "synthetic", "max_input": 1.0, **(meta or {})},
    )
    return Project(
        schema_version="2.0.0",
        stages=(stage,),
        analysis=Analysis(source_frequency_hz=10e6, input_power_dbm=-20),
        lab={"models": {"a": {"harmonic": model}}, "settings": settings or {}},
    )


def wave(result, frequency):
    row = next(p for p in result["points"] if p["frequency_hz"] == frequency)
    return complex(row["wave_real"], row["wave_imag"])


def test_hb_single_tone_fourier_coefficients_and_dc_power():
    p = hb_project()
    r = harmonic.solve(p, 1e6, 64)
    amplitude = np.sqrt(2 * 0.01)
    expected = {
        0: 0.1 * amplitude**2 / 2,
        10e6: (2 * amplitude - 3 * 0.2 * amplitude**3 / 4) / 2,
        20e6: 0.1 * amplitude**2 / 4,
        30e6: -0.2 * amplitude**3 / 8,
    }
    for f, value in expected.items():
        assert wave(r, f) == pytest.approx(value, abs=1e-12)
    dc = r["points"][0]
    assert 10 ** (dc["power_dbm"] / 10) == pytest.approx(expected[0] ** 2)
    assert r["solver"]["relative_residual"] <= 1e-9
    assert loads(dumps(p)) == p
    json.dumps(r, allow_nan=False)


def test_hb_two_tone_im2_and_im3_from_independent_trigonometry():
    p = hb_project()
    source = [{"frequency_hz": f, "power_dbm": -30} for f in (9e6, 11e6)]
    r = harmonic.solve(p, 1e6, 64, source)
    a = np.sqrt(0.002)
    assert wave(r, 2e6) == pytest.approx(0.1 * a * a / 2, abs=1e-12)
    assert wave(r, 20e6) == pytest.approx(0.1 * a * a / 2, abs=1e-12)
    assert wave(r, 7e6) == pytest.approx(-3 * 0.2 * a**3 / 8, abs=1e-12)
    assert wave(r, 13e6) == pytest.approx(-3 * 0.2 * a**3 / 8, abs=1e-12)


def test_hb_cascaded_quadratic_models_generate_cubic_products():
    p = hb_project((2.0, 0.1))
    s = replace(p.stages[0], id="b", order=2)
    model = {"kind": "harmonic", "coefficients": [3.0, 0.2], "metadata": {"max_input": 2.0}}
    p = replace(
        p,
        stages=(*p.stages, s),
        lab={"models": {**p.lab["models"], "b": {"harmonic": model}}, "settings": {}},
    )
    r = harmonic.solve(p, 1e6, 64)
    a = np.sqrt(0.02)
    assert wave(r, 30e6) == pytest.approx((2 * 0.2 * 2 * 0.1) * a**3 / 8, rel=1e-7)


def test_hb_feedback_matches_independent_time_domain_roots():
    meta = {"s11": 0.1, "s12": 0.2, "s22": -0.1}
    settings = {"source_gamma": [0.3, 0], "load_gamma": [0.2, 0]}
    p = hb_project((2.0, 0.1, -0.2), meta, settings=settings)
    r = harmonic.solve(p, 1e6, 64)
    t = np.arange(4096) / 4096
    source = np.sqrt(0.02 * (1 - 0.3**2)) * np.cos(2 * np.pi * 10 * t)
    poly = lambda x: 2 * x + 0.1 * x * x - 0.2 * x * x * x
    a = np.array(
        [
            brentq(
                lambda x: (1 - 0.3 * 0.1) * x - v - 0.3 * 0.2 * 0.2 * poly(x) / (1 + 0.1 * 0.2),
                -1,
                1,
                xtol=1e-14,
            )
            for v in source
        ]
    )
    y = poly(a) / (1 + 0.1 * 0.2)
    spectrum = np.fft.rfft(y) / len(y)
    for f in (0, 10e6, 20e6, 30e6):
        assert wave(r, f) == pytest.approx(spectrum[round(f / 1e6)], abs=2e-10)
    assert r["solver"]["iterations"] > 0


def test_hb_mixer_retains_sum_difference_and_phase():
    p = hb_project((2.0,), mixer=Mixer(lo_frequency_hz=7e6))
    r = harmonic.solve(p, 1e6, 32, [{"frequency_hz": 10e6, "power_dbm": -20, "phase_deg": 30}])
    expected = 2 * np.sqrt(0.01 / 2) * np.exp(1j * np.deg2rad(30))
    assert wave(r, 3e6) == pytest.approx(expected, abs=1e-12)
    assert wave(r, 17e6) == pytest.approx(expected, abs=1e-12)


def test_hb_output_pole_has_analytic_gain_and_phase():
    p = hb_project((2.0,), {"pole_hz": 5e6})
    r = harmonic.solve(p, 1e6, 32)
    assert wave(r, 10e6) == pytest.approx(2 * np.sqrt(0.01 / 2) / (1 + 2j), abs=1e-12)


def test_hb_feedback_with_dynamic_pole_matches_independent_transient_ode():
    p = hb_project(
        (2.0, 0.1, -0.2),
        {"pole_hz": 20e6, "s11": 0.1, "s12": 0.2, "s22": -0.1},
        settings={"source_gamma": [0.3, 0], "load_gamma": [0.2, 0]},
    )
    r = harmonic.solve(p, 1e6, 64)

    def derivative(t, y):
        source = np.sqrt(0.02 * (1 - 0.3**2)) * np.cos(2 * np.pi * 10 * t)
        a = (source + 0.3 * 0.2 * 0.2 * y[0]) / (1 - 0.3 * 0.1)
        value = (2 * a + 0.1 * a * a - 0.2 * a * a * a) / (1 + 0.1 * 0.2)
        return [2 * np.pi * 20 * (value - y[0])]

    times = 4 + np.arange(4096) / 4096
    transient = solve_ivp(
        derivative, (0, 5), [0.0], t_eval=times, rtol=1e-10, atol=1e-12, max_step=1 / 500, method="DOP853"
    )
    assert transient.success
    reference = np.fft.rfft(transient.y[0]) / 4096
    for f in (0, 10e6, 20e6, 30e6):
        assert wave(r, f) == pytest.approx(reference[round(f / 1e6)], abs=2e-10)


def test_hb_linear_reflection_matches_independent_network_power_and_nonconvergence_rejects():
    from rf_link_calculator.advanced.network import terminated

    p = hb_project(
        (2.0,),
        {"s11": 0.1, "s12": 0.2, "s22": -0.1},
        settings={"source_gamma": [0.3, 0], "load_gamma": [0.2, 0]},
    )
    r = harmonic.solve(p, 1e6, 32)
    gain = terminated(np.array([[0.1, 0.2], [2.0, -0.1]]), None, 0.3, 0.2)["gain_db"]
    point = next(x for x in r["points"] if x["frequency_hz"] == 10e6)
    assert point["power_dbm"] == pytest.approx(-20 + gain, abs=1e-10)
    nonlinear = hb_project(
        (2.0, 0.1, -0.2), {"s11": 0.1, "s12": 0.2, "s22": -0.1}, settings=p.lab["settings"]
    )
    with pytest.raises(ValueError, match="未收敛"):
        harmonic.solve(nonlinear, 1e6, 64, tolerance=1e-12, max_iterations=1)


def test_hb_truncation_detects_omitted_products_and_converges_when_extended():
    p = hb_project()
    r = harmonic.convergence(p, 1e6, 16)
    assert r["convergence"]["status"] == "fail"
    assert r["convergence"]["new_out_of_band_components"]
    assert harmonic.convergence(p, 1e6, 32)["convergence"]["status"] == "pass"


def test_hb_rejects_missing_models_bad_grid_range_and_wrong_analysis():
    p = hb_project()
    with pytest.raises(ValueError, match="栅格"):
        harmonic.solve(p, 3e6, 32)
    with pytest.raises(ValueError, match="峰值"):
        harmonic.solve(replace(p, analysis=replace(p.analysis, input_power_dbm=0)), 1e6, 64)
    with pytest.raises(ValueError, match="独立谐波"):
        harmonic.solve(replace(p, lab={}), 1e6, 64)
    with pytest.raises(ValueError, match="谐波分析"):
        tones(p)
    with pytest.raises(ValueError, match="叠加"):
        d = p.to_dict()
        d["lab"]["models"]["a"]["nonlinear"] = {
            "kind": "ampm",
            "pin_dbm": [-20, 0],
            "pout_dbm": [-10, 10],
            "phase_deg": [0, 0],
        }
        project_from_dict(d)
    assert (
        dispatch("harmonic", p, {"fundamental_hz": 1e6, "harmonics": 64})["solver"]["status"] == "converged"
    )


def noise_project(pre_gain=10, pre_nf=2):
    a = Stage(id="pre", order=1, gain_db=pre_gain, noise=NoiseSpec("manual", pre_nf))
    m = Stage(
        id="mix",
        order=2,
        type="mixer",
        gain_db=-6,
        noise=NoiseSpec("manual", 4),
        mixer=Mixer(lo_frequency_hz=90e6, noise_convention="SSB", noise_compatible=True),
    )
    return Project(
        schema_version="2.0.0",
        stages=(a, m),
        analysis=Analysis(source_frequency_hz=100e6),
        lab={"models": {}, "settings": {}},
    )


def test_automatic_image_noise_counts_two_branches_without_double_counting_ssb():
    p = noise_project()
    r = automatic_image_noise(p)
    # Two equal amplified source bands, each G*F*kT; intrinsic mixer Fssb-2.
    expected = 2 * 10 ** (0.2) + (10 ** (0.4) - 2) / 10
    assert r["nf_db"] == pytest.approx(10 * np.log10(expected))
    assert r["image_frequency_hz"] == 80e6
    bare = replace(p, stages=(p.stages[1],))
    assert automatic_image_noise(bare)["nf_db"] == pytest.approx(4)
    assert sum(x["density_w_hz"] for x in r["contributions"]) * 1e6 * 1000 == pytest.approx(
        10 ** (r["noise_output_dbm"] / 10)
    )


def test_automatic_image_noise_uses_frequency_response_and_rejects_inconsistent_nf():
    p = noise_project(pre_gain=0, pre_nf=0)
    curve = import_dataset("frequency", "frequency_hz,gain_db,nf_db\n80000000,-20,20\n100000000,0,0\n")
    p = replace(p, lab={"models": {"pre": {"linear": curve}}, "settings": {}})
    # A matched thermal passive image filter at T0 still delivers kT noise.
    assert automatic_image_noise(p)["nf_db"] == pytest.approx(4)
    m = replace(p.stages[1], noise=NoiseSpec("manual", 1))
    with pytest.raises(ValueError, match="SSB"):
        automatic_image_noise(replace(p, stages=(p.stages[0], m)))
    with pytest.raises(ValueError, match="范围"):
        automatic_image_noise(replace(p, analysis=replace(p.analysis, source_frequency_hz=110e6)))


def test_image_temperature_scales_only_its_noise_branch_and_new_examples_round_trip():
    from io import BytesIO
    from zipfile import ZipFile

    from rf_link_calculator.advanced.benchmark import handoff, rows_for
    from rf_link_calculator.advanced.harmonic_examples import examples
    from rf_link_calculator.advanced.network import KB

    p = noise_project()
    cold, hot = automatic_image_noise(p, 100), automatic_image_noise(p, 600)
    delta = (10 ** (hot["noise_output_dbm"] / 10) - 10 ** (cold["noise_output_dbm"] / 10)) / 1000
    assert delta == pytest.approx(
        KB * 500 * 10 ** (hot["image_gain_db"] / 10) * p.analysis.noise_bandwidth_hz
    )
    for name, p in examples().items():
        assert loads(dumps(p)) == p
        analysis = (
            {"kind": "image-auto", "parameters": {}}
            if name == "镜像噪声链路"
            else {"kind": "harmonic", "parameters": {"fundamental_hz": 1e6, "harmonics": 64}}
        )
        rows = rows_for(p, analysis)
        assert rows
        with ZipFile(BytesIO(handoff(p, analysis))) as z:
            assert loads(z.read("project.json")) == p
            assert json.loads(z.read("case.json"))["analysis"] == analysis


def test_harmonic_reference_cannot_hide_large_relative_error_in_weak_products():
    from rf_link_calculator.advanced.benchmark import compare, rows_for
    from rf_link_calculator.domain.codec import hashes

    p = hb_project()
    analysis = {"kind": "harmonic", "parameters": {"fundamental_hz": 1e6, "harmonics": 64}}
    rows = rows_for(p, analysis)
    weak = next(r for r in rows if r["metric"] == "wave_real" and r["scope"] == "frequency:30000000")
    weak["value"] = str(float(weak["value"]) + 1e-7)
    reference = {
        "kind": "reference",
        "rows": rows,
        "metadata": {
            "software": "Test reference",
            "version": "1",
            "solver": "analytic",
            "source": "synthetic",
            "analysis": analysis,
            "calculation_hash": hashes(p)[1],
        },
    }
    report = compare(p, reference)
    assert report["status"] == "incomplete_or_failed"
    assert (
        next(r for r in report["rows"] if r["metric"] == "wave_real" and r["scope"] == "frequency:30000000")[
            "comparison"
        ]
        == "fail"
    )
