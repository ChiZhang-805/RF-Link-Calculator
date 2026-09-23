import json
from dataclasses import replace
from io import BytesIO
from zipfile import ZipFile

import numpy as np
import pytest
import skrf as rf

from rf_link_calculator.advanced import benchmark
from rf_link_calculator.advanced.api import dispatch
from rf_link_calculator.advanced.data import import_dataset, validate_dataset
from rf_link_calculator.advanced.network import cascade, evaluate, noise_wave, s_at, terminated
from rf_link_calculator.advanced.nonlinear import apply_model, basis, fit_memory, wave_metrics
from rf_link_calculator.advanced.spectral import image_noise, spur_table, tones
from rf_link_calculator.domain.codec import hashes, project_from_dict
from rf_link_calculator.domain.models import Analysis, Mixer, NoiseSpec, PowerSpec, Project, Stage, TwoTone
from rf_link_calculator.engine import calculate
from rf_link_calculator.examples import examples
from rf_link_calculator.exporters.bundle import export_bundle
from rf_link_calculator.persistence.json_io import dumps, loads


def stage(**kw):
    return Stage(
        gain_db=10,
        noise=NoiseSpec("manual", 2),
        ip3=PowerSpec("finite", "input", 10),
        p1db=PowerSpec("finite", "input", 0),
        **kw,
    )


def advanced(p, models=None, settings=None, **data):
    return replace(
        p, schema_version="2.0.0", lab={"models": models or {}, "settings": settings or {}, **data}
    )


def network_data(s, noise=None, z=50):
    ds = {
        "kind": "sparameter",
        "frequency_hz": [90e6, 110e6],
        "s_real": [s.real.tolist()] * 2,
        "s_imag": [s.imag.tolist()] * 2,
        "z0_ohm": [[z, z]] * 2,
        "metadata": {},
    }
    if noise:
        ds["noise"] = noise
    return ds


def test_v1_v2_roundtrip_and_reference_does_not_change_calculation():
    p = examples()["接收链路"]
    q = advanced(p)
    assert loads(dumps(q)) == q
    assert hashes(p)[1] == hashes(q)[1]
    ref = {"kind": "reference", "rows": benchmark.rows_for(p), "metadata": {}}
    q = advanced(p, reference=ref)
    assert hashes(q)[1] == hashes(p)[1]
    with pytest.raises(ValueError):
        project_from_dict({**q.to_dict(), "lab": {"models": {"not-a-stage": {}}}})


def test_frequency_interpolates_phase_and_refuses_extrapolation():
    s = stage()
    ds = import_dataset(
        "frequency", "frequency_hz,gain_db,nf_db,phase_deg\n90000000,10,2,170\n110000000,20,4,-170\n"
    )
    p = advanced(Project(stages=(s,)), {s.id: {"linear": ds}})
    result = calculate(p)
    assert result.metrics["gain_db"].value == pytest.approx(15)
    assert result.metrics["nf_db"].value == pytest.approx(3)
    assert abs(evaluate(p)[0]["phase_deg"]) == pytest.approx(180)
    with pytest.raises(ValueError, match="范围"):
        evaluate(p, 120e6)


def test_complex_cascade_matches_independent_skrf_and_termination_formula():
    a = np.array([[0.12j, 0.03], [2 * np.exp(0.2j), -0.1j]])
    b = np.array([[0.2, 0.01j], [0.6, 0.12j]])
    actual, _ = cascade(a, None, b, None)
    rfa = rf.Network(f=[1e8], s=[a], z0=50)
    rfb = rf.Network(f=[1e8], s=[b], z0=50)
    assert actual == pytest.approx((rfa**rfb).s[0], abs=1e-12)
    gs, gl = 0.25j, -0.17
    d = (1 - actual[0, 0] * gs) * (1 - actual[1, 1] * gl) - actual[0, 1] * actual[1, 0] * gs * gl
    expected = (1 - abs(gs) ** 2) * (1 - abs(gl) ** 2) * abs(actual[1, 0] / d) ** 2
    assert terminated(actual, None, gs, gl)["gain_db"] == pytest.approx(10 * np.log10(expected))


def test_passive_noise_covariance_matches_thermal_friis():
    t = 10 ** (-3 / 20)
    ds = network_data(np.array([[0, t], [t, 0]], complex))
    s = Stage(
        type="attenuator",
        gain_db=-3,
        noise=NoiseSpec("passive_thermal"),
        ip3=PowerSpec("ideal"),
        p1db=PowerSpec("ideal"),
        physical_temperature_k=350,
    )
    p = advanced(Project(stages=(s,)), {s.id: {"linear": ds}})
    expected = 10 * np.log10(1 + (10**0.3 - 1) * 350 / 290)
    assert evaluate(p)[0]["nf_db"] == pytest.approx(expected, abs=1e-10)
    # Reverse coupling makes scalar nonlinear metrics unavailable, not fake accurate.
    assert calculate(p).metrics["ip1_dbm"].value is None


def test_noise_parameters_agree_with_noise_factor_equation():
    s = np.array([[0.15j, 0.02], [3, 0.1]], complex)
    noise = {
        "frequency_hz": [90e6, 110e6],
        "nfmin_db": [1, 1],
        "gamma_mag": [0.2, 0.2],
        "gamma_deg": [30, 30],
        "rn_ohm": [10, 10],
    }
    ds = network_data(s, noise)
    c = noise_wave(ds, s, 1e8)
    gs = 0.25 - 0.1j
    go = 0.2 * np.exp(1j * np.deg2rad(30))
    expected = 10**0.1 + 4 * 10 / 50 * abs(gs - go) ** 2 / ((1 - abs(gs) ** 2) * abs(1 + go) ** 2)
    assert terminated(s, c, gs)["nf_db"] == pytest.approx(10 * np.log10(expected), abs=1e-10)
    # NF is load-independent for a fixed source in this linear two-port convention.
    assert terminated(s, c, gs, 0.2j)["nf_db"] == pytest.approx(10 * np.log10(expected), abs=1e-10)


def test_touchstone_v1_noise_normalization_and_renormalization():
    text = "# MHz S RI R 75\n90 0 0 2 0 0 0 0 0\n110 0 0 2 0 0 0 0 0\n90 1 0.2 30 0.2\n110 1 0.2 30 0.2\n"
    ds = import_dataset("sparameter", text, "amp.s2p")
    assert ds["noise"]["rn_ohm"] == [15, 15]
    assert ds["frequency_hz"] == [90e6, 110e6]
    original = rf.Network(f=[1e8], s=[[[0, 0], [2, 0]]], z0=75)
    original.renormalize(50)
    assert s_at(ds, 1e8) == pytest.approx(original.s[0])


def test_missing_active_noise_does_not_assume_zero():
    s = stage()
    ds = network_data(np.array([[0.1, 0], [2, 0]], complex))
    p = advanced(Project(stages=(s,)), {s.id: {"linear": ds}})
    result = calculate(p)
    assert result.metrics["gain_db"].value == pytest.approx(20 * np.log10(2))
    assert result.metrics["nf_db"].status == "unknown"
    assert result.metrics["sfdr3_db"].value is None


def test_ampm_cw_p1_and_complex_phase():
    model = import_dataset("ampm", "pin_dbm,pout_dbm,phase_deg\n-80,-60,0\n-20,0,0\n-10,9,10\n0,15,30\n")
    s = stage()
    p = advanced(Project(analysis=Analysis(input_power_dbm=-10), stages=(s,)), {s.id: {"nonlinear": model}})
    result = calculate(p)
    assert result.metrics["gain_db"].value == pytest.approx(20)
    assert result.metrics["compressed_output_dbm"].value == pytest.approx(9)
    assert result.metrics["ip1_dbm"].value == pytest.approx(-10)
    y = apply_model([np.sqrt(0.1)], model)
    assert np.angle(y[0], deg=True) == pytest.approx(10)
    with pytest.raises(ValueError, match="范围"):
        apply_model([2], model)


def test_fft_two_tone_matches_cubic_intermodulation_and_converges():
    s = stage()
    p = Project(stages=(s,), analysis=Analysis(two_tone=TwoTone(True, -30, 1e5)))
    first = tones(p, 2048)
    second = tones(p, 8192)
    for offset in [-150e3, 150e3]:
        r = next(r for r in first["points"] if r["offset_hz"] == pytest.approx(offset))
        expected = 3 * -30 + 10 - 2 * 10
        assert r["power_dbm"] == pytest.approx(expected, abs=1e-8)
        other = next(r for r in second["points"] if r["offset_hz"] == pytest.approx(offset))
        assert other["power_dbm"] == pytest.approx(r["power_dbm"], abs=1e-8)


def iq_data(x, y):
    return {
        "kind": "iq",
        "i_in": x.real.tolist(),
        "q_in": x.imag.tolist(),
        "i_out": y.real.tolist(),
        "q_out": y.imag.tolist(),
        "metadata": {"sha256": "synthetic"},
    }


def test_memory_polynomial_identification_and_heldout_validation():
    rng = np.random.default_rng(617)
    x = (rng.normal(size=4096) + 1j * rng.normal(size=4096)) * 0.1
    co = np.array([2 + 0.1j, 0.2j, -0.5, 0.1 + 0.04j])
    y = basis(x, [1, 3], 2) @ co
    model = fit_memory(iq_data(x, y), 10e6, 3, 2)
    assert model["metadata"]["validation"]["nmse_db"] < -200
    predicted = apply_model(x[:2048], model, 10e6)
    assert predicted == pytest.approx(y[:2048], abs=1e-12)
    assert model["metadata"]["validation_start"] > 2048
    with pytest.raises(ValueError, match="采样率"):
        apply_model(x[:100], model, 20e6)
    with pytest.raises(ValueError, match="训练范围"):
        apply_model([10], model)


def test_unidentifiable_iq_is_rejected_and_nonfinite_data_rejected():
    with pytest.raises(ValueError, match="秩不足"):
        fit_memory(iq_data(np.ones(1024, complex), np.ones(1024, complex)), 1e6, 5, 3)
    with pytest.raises(ValueError):
        import_dataset("ampm", "pin_dbm,pout_dbm,phase_deg\n-10,NaN,0\n0,10,0\n")
    with pytest.raises(ValueError):
        validate_dataset({"kind": "memory", "orders": [1], "depth": 0})


def test_waveform_acpr_and_gain_aligned_evm_have_known_answer():
    n, fs = 8192, 8.192e6
    t = np.arange(n) / fs
    x = np.exp(2j * np.pi * 100e3 * t)
    y = 2 * x + 0.02 * np.exp(2j * np.pi * 2.1e6 * t)
    result = wave_metrics(x, y, fs, 500e3, 2e6)["metrics"]
    assert result["acpr_right_dbc"] == pytest.approx(-40, abs=0.001)
    assert result["evm_gain_aligned_percent"] == pytest.approx(1, abs=1e-6)


def test_imt_preserves_products_paths_and_requires_measured_drive():
    s = Stage(type="mixer", gain_db=-10, mixer=Mixer(lo_frequency_hz=2.3e9, lo_power_dbm=10))
    ds = import_dataset(
        "imt",
        "m,n,output_dbm\n1,-1,-30\n2,-1,-60\n",
        metadata={"rf_hz": 2.4e9, "lo_hz": 2.3e9, "rf_dbm": -20, "lo_dbm": 10},
    )
    p = advanced(
        Project(stages=(s,), analysis=Analysis(source_frequency_hz=2.4e9, input_power_dbm=-20)),
        {s.id: {"mixer": ds}},
    )
    result = spur_table(p)
    assert [r["frequency_hz"] for r in result["points"]] == [1e8, 2.5e9]
    assert result["points"][0]["origin"] == s.id
    with pytest.raises(ValueError, match="条件"):
        spur_table(replace(p, analysis=replace(p.analysis, input_power_dbm=-21)))


def test_image_noise_replaces_290k_contribution_without_double_counting():
    s = Stage(
        type="mixer",
        gain_db=-6,
        noise=NoiseSpec("manual", 6),
        mixer=Mixer(lo_frequency_hz=2.3e9, noise_convention="SSB", noise_compatible=True),
    )
    p = Project(stages=(s,), analysis=Analysis(source_frequency_hz=2.4e9))
    ordinary = calculate(p).metrics["noise_output_dbm"].value
    assert image_noise(p, -6, 290)["noise_output_dbm"] == pytest.approx(ordinary)
    assert image_noise(p, -6, 0)["noise_output_dbm"] < ordinary


def test_external_benchmark_checks_fingerprints_units_missing_and_source():
    p = examples()["接收链路"]
    ref = {
        "kind": "reference",
        "rows": benchmark.rows_for(p),
        "metadata": {
            "calculation_hash": hashes(p)[1],
            "software": "RF Link",
            "version": "2",
            "solver": "Friis",
            "source": "synthetic",
        },
    }
    assert benchmark.compare(p, ref)["status"] == "internal_only"
    ref["metadata"].update(software="Independent fixture", source="independent")
    assert benchmark.compare(p, ref)["status"] == "pass"
    ref["rows"][0]["value"] = str(float(ref["rows"][0]["value"]) + 0.01)
    assert benchmark.compare(p, ref)["status"] == "incomplete_or_failed"
    ref["metadata"]["calculation_hash"] = "stale"
    with pytest.raises(ValueError, match="散列"):
        benchmark.compare(p, ref)
    with ZipFile(BytesIO(benchmark.handoff(p))) as z:
        assert "run_reference.m" in z.namelist()
        script = z.read("run_reference.m").decode()
        assert script.count("for k = 1:5") == 2
        assert "rows(end-5+k" not in script
        assert json.loads(z.read("case.json"))["source"] == "internal_generated_not_external"


def test_advanced_api_and_export_are_portable_snapshots():
    p = Project(stages=(stage(),))
    response = dispatch(
        "import",
        p,
        {
            "kind": "frequency",
            "text": "frequency_hz,gain_db\n90000000,10\n110000000,12\n",
            "stage_id": p.stages[0].id,
        },
    )
    q = project_from_dict(response["project"])
    result = calculate(q)
    assert result.metrics["gain_db"].value == pytest.approx(11)
    raw, manifest = export_bundle(q, result)
    assert manifest["status"] == "success"
    assert any("静态快照" in f.get("capability", "") for f in manifest["files"])
    with ZipFile(BytesIO(raw)) as z:
        assert loads(z.read("链路输入.json")) == q
        assert "模型与数据.json" in z.namelist()
        assert "外部对标.zip" in z.namelist()


def test_integrated_noise_flat_band_equals_ktb_friis():
    from rf_link_calculator.advanced.network import integrated_noise

    p = examples()["接收链路"]
    result = integrated_noise(p, 99.5e6, 100.5e6, 51)
    assert result["integrated_noise_dbm"] == pytest.approx(
        calculate(p).metrics["noise_output_dbm"].value, abs=1e-10
    )


def test_advanced_compression_scan_uses_frequency_projected_gain():
    from rf_link_calculator.presentation.server import dispatch as http_dispatch

    s = stage()
    ds = import_dataset("frequency", "frequency_hz,gain_db,ip1_dbm\n90000000,20,0\n110000000,20,0\n")
    p = advanced(Project(stages=(s,)), {s.id: {"linear": ds}})
    result = http_dispatch("scan", {"project": p.to_dict(), "low": -60, "high": -50})
    assert result["points"][0]["compressed"] == pytest.approx(-40, abs=1e-6)


def test_measured_p1_replaces_previous_scalar_stage_value():
    s = stage()
    ds = import_dataset("ampm", "pin_dbm,pout_dbm,phase_deg\n-80,-60,0\n-20,0,0\n-10,9,2\n0,15,10\n")
    p = advanced(Project(stages=(s,)), {s.id: {"nonlinear": ds}})
    assert calculate(p).stages[0].metrics["stage_ip1_dbm"].value == pytest.approx(-10)


def test_conflicting_models_do_not_report_false_compression():
    from rf_link_calculator.advanced.examples import examples as model_examples

    p = model_examples()["功率曲线链路"]
    p.lab["models"][p.stages[0].id]["linear"] = import_dataset(
        "frequency", "frequency_hz,gain_db\n90000000,10\n110000000,10\n"
    )
    result = calculate(p)
    assert result.metrics["gain_db"].value == pytest.approx(10)
    assert result.metrics["compressed_output_dbm"].value is None


def test_sweep_benchmark_uses_complex_values_and_wraps_phase():
    from rf_link_calculator.advanced.examples import examples as model_examples

    p = model_examples()["扫频链路"]
    analysis = {"kind": "sweep", "parameters": {"low": 90e6, "high": 110e6, "count": 3}}
    rows = benchmark.rows_for(p, analysis)
    assert any(r["metric"] == "s21_real" for r in rows)
    for row in rows:
        if row["metric"] == "phase_deg":
            row["value"] = str(float(row["value"]) + 360)
    ref = {
        "kind": "reference",
        "rows": rows,
        "metadata": {
            "analysis": analysis,
            "calculation_hash": hashes(p)[1],
            "software": "Synthetic reference",
            "version": "1",
            "solver": "fixture",
            "source": "synthetic",
        },
    }
    result = benchmark.compare(p, ref)
    assert result["status"] == "internal_only"
    with ZipFile(BytesIO(benchmark.handoff(p, analysis))) as z:
        assert "run_reference.m" not in z.namelist()
        assert json.loads(z.read("case.json"))["analysis"] == analysis


def test_all_synthetic_model_projects_export_and_restore():
    from rf_link_calculator.advanced.examples import examples as model_examples

    for p in model_examples().values():
        assert loads(dumps(p)) == p
        result = calculate(p)
        _, manifest = export_bundle(p, result)
        assert manifest["status"] == "success"


def test_advanced_csv_export_cannot_drop_models_silently():
    from rf_link_calculator.advanced.examples import examples as model_examples
    from rf_link_calculator.presentation.server import dispatch as http_dispatch

    p = model_examples()["扫频链路"]
    with pytest.raises(ValueError, match="模型数据"):
        http_dispatch("export", {"project": p.to_dict(), "kind": "csv", "project_hash": hashes(p)[0]})


def test_fft_convergence_really_increases_sample_rate():
    from rf_link_calculator.advanced.spectral import convergence

    p = Project(stages=(stage(),), analysis=Analysis(two_tone=TwoTone(True, -30, 1e5)))
    result = convergence(p, 4096)
    assert result["convergence"]["status"] == "pass"
    assert result["convergence"]["sample_rates_hz"] == [3.2e6, 6.4e6]
    assert len(result["convergence"]["components"]) >= 4


def test_high_side_lo_inverts_unequal_tones_and_preserves_path():
    before = Stage(
        id="before", gain_db=0, noise=NoiseSpec("ideal"), ip3=PowerSpec("ideal"), p1db=PowerSpec("ideal")
    )
    mixer = replace(before, id="mixer", order=2, type="mixer", gain_db=-6, mixer=Mixer(lo_frequency_hz=2.5e9))
    p = Project(analysis=Analysis(source_frequency_hz=2.4e9), stages=(before, mixer))
    result = tones(
        p, 4096, "linear", [{"offset_hz": -50000, "power_dbm": -30}, {"offset_hz": 50000, "power_dbm": -40}]
    )
    strong = max(result["points"], key=lambda p: p["power_dbm"])
    assert strong["frequency_hz"] == pytest.approx(100.05e6)
    assert strong["power_dbm"] == pytest.approx(-36)
    assert strong["path"][0]["frequency_hz"] == pytest.approx(2.39995e9)
    assert strong["path"][0]["power_dbm"] == pytest.approx(-30)


def test_frequency_curves_use_local_if_after_mixing():
    mixer = Stage(
        id="mixer",
        type="mixer",
        gain_db=-6,
        noise=NoiseSpec("manual", 6),
        mixer=Mixer(lo_frequency_hz=2.3e9, noise_convention="SSB", noise_compatible=True),
    )
    after = stage(id="if", order=2)
    ds = import_dataset("frequency", "frequency_hz,gain_db\n90000000,11\n110000000,13\n")
    p = advanced(
        Project(analysis=Analysis(source_frequency_hz=2.4e9), stages=(mixer, after)),
        {after.id: {"linear": ds}},
    )
    result = evaluate(p)[0]
    assert result["gain_db"] == pytest.approx(6)
    assert result["stages"][1]["frequency_input_hz"] == pytest.approx(1e8)


@pytest.mark.parametrize(
    "encoding,values",
    [
        ("RI", "0 0 2 0 0 0 0 0"),
        ("MA", "0 0 2 0 0 0 0 0"),
        ("DB", "-300 0 6.020599913279624 0 -300 0 -300 0"),
    ],
)
def test_touchstone_encodings_have_identical_forward_gain(encoding, values):
    ds = import_dataset(
        "sparameter", f"# GHz S {encoding} R 50\n0.09 {values}\n0.11 {values}\n", "fixture.s2p"
    )
    assert s_at(ds, 1e8)[1, 0] == pytest.approx(2, abs=1e-12)


def test_touchstone_two_noise_resistance_is_in_ohms():
    text = "[Version] 2.0\n# MHz S RI R 50\n[Number of Ports] 2\n[Two-Port Data Order] 21_12\n[Number of Frequencies] 2\n[Number of Noise Frequencies] 2\n[Reference] 50 50\n[Network Data]\n90 0 0 2 0 0 0 0 0\n110 0 0 2 0 0 0 0 0\n[Noise Data]\n90 1 0.2 30 10\n110 1 0.2 30 10\n[End]\n"
    ds = import_dataset("sparameter", text, "fixture.ts")
    assert ds["noise"]["rn_ohm"] == [10, 10]


def test_curve_reference_compares_output_and_compression_separately():
    from rf_link_calculator.advanced.examples import examples as model_examples

    p = model_examples()["功率曲线链路"]
    analysis = {"kind": "power", "parameters": {"low": -20, "high": -10, "count": 3}}
    rows = benchmark.rows_for(p, analysis)
    assert len(rows) == 6
    ref = {
        "kind": "reference",
        "rows": rows,
        "metadata": {
            "analysis": analysis,
            "calculation_hash": hashes(p)[1],
            "software": "Synthetic fixture",
            "version": "1",
            "solver": "curve",
            "source": "synthetic",
        },
    }
    result = benchmark.compare(p, ref)
    assert result["status"] == "internal_only"
    assert result["error_summary"]["pout_dbm"]["max_abs_error"] == 0


def test_curve_out_of_range_never_retains_scalar_compression_rows():
    from rf_link_calculator.advanced.examples import examples as model_examples

    p = model_examples()["功率曲线链路"]
    p = replace(p, analysis=replace(p.analysis, input_power_dbm=10))
    result = calculate(p)
    assert result.metrics["compressed_output_dbm"].value is None
    assert result.stages[0].metrics["compressed_output_dbm"].value is None
    assert result.stages[0].metrics["stage_compression_db"].value is None


def test_matlab_stage_ids_are_encoded_data():
    p = Project(stages=(stage(id="'\ncommand(); %"),))
    script = benchmark.matlab_script(p)
    assert "command()" not in script
    assert "native2unicode(uint8([" in script


def test_published_vendor_examples_match_without_claiming_local_matlab_run():
    from rf_link_calculator.advanced.reference_cases import examples as reference_examples

    for p in reference_examples().values():
        assert loads(dumps(p)) == p
        r = benchmark.compare(p, p.lab["reference"])
        assert r["status"] == "published_match"
        assert not r["independent_source_declared"]
        assert len(r["rows"]) == 20
        assert all(x["comparison"] == "pass" for x in r["rows"])
        script = benchmark.matlab_script(p)
        assert "modulator(" in script
        assert "elements = [e1 e2 e3]" in script


def test_external_validation_does_not_leak_into_memory_training():
    rng = np.random.default_rng(715)
    x = (rng.normal(size=2048) + 1j * rng.normal(size=2048)) * 0.1
    v = (rng.normal(size=1024) + 1j * rng.normal(size=1024)) * 0.05
    coefficients = np.array([2 + 0.1j, 0.2j, -0.5, 0.1])
    train = iq_data(x, basis(x, [1, 3], 2) @ coefficients)
    valid = iq_data(v, basis(v, [1, 3], 2) @ coefficients)
    m = fit_memory(train, 10e6, 3, 2, validation_data=valid)
    assert m["metadata"]["train_samples"] == 2047
    assert m["metadata"]["validation_samples"] == 1023
    assert m["metadata"]["validation_mode"] == "external_record"
    assert m["metadata"]["validation"]["nmse_db"] < -200
    changed = fit_memory(
        train,
        10e6,
        3,
        2,
        validation_data=iq_data(v, np.asarray(valid["i_out"]) + 1j * np.asarray(valid["q_out"]) + 0.02),
    )
    assert changed["coefficients"] == m["coefficients"]
    assert changed["metadata"]["validation"]["nmse_db"] > -30


def test_external_iq_validation_rejects_out_of_domain_and_unit_mismatch():
    rng = np.random.default_rng(718)
    x = rng.uniform(-0.1, 0.1, 1024) + 1j * rng.uniform(-0.1, 0.1, 1024)
    data = iq_data(x, x * 2)
    with pytest.raises(ValueError, match="峰值"):
        fit_memory(data, 1e6, 1, 1, validation_data=iq_data(x * 10, x * 20))
    mismatched = iq_data(x, x * 2)
    mismatched["metadata"]["amplitude_unit"] = "normalized"
    with pytest.raises(ValueError, match="幅度单位"):
        fit_memory(data, 1e6, 1, 1, validation_data=mismatched)


def test_normalized_iq_is_not_silently_treated_as_milliwatts():
    rng = np.random.default_rng(718)
    x = rng.uniform(-0.1, 0.1, 1024) + 1j * rng.uniform(-0.1, 0.1, 1024)
    data = iq_data(x, x * 2)
    data["metadata"]["amplitude_unit"] = "normalized"
    model = fit_memory(data, 1e6, 1, 1)
    s = stage()
    with pytest.raises(ValueError, match="已标定"):
        project_from_dict(advanced(Project(stages=(s,)), {s.id: {"nonlinear": model}}).to_dict())
    with pytest.raises(ValueError, match="已标定"):
        project_from_dict(advanced(Project(stages=(s,)), iq=data).to_dict())


def test_gain_only_data_cannot_report_measured_phase_or_complex_transmission():
    s = stage()
    ds = import_dataset("frequency", "frequency_hz,gain_db\n90000000,10\n110000000,20\n")
    p = advanced(Project(stages=(s,)), {s.id: {"linear": ds}})
    values, _ = evaluate(p)
    assert values["gain_db"] == pytest.approx(15)
    for key in ("phase_deg", "s21_real", "s21_imag"):
        assert values[key] is None
        assert values["stages"][0][key] is None
    reference_rows = benchmark.rows_for(
        p, {"kind": "sweep", "parameters": {"low": 90e6, "high": 110e6, "count": 2}}
    )
    assert all(r["status"] == "unknown" for r in reference_rows if r["metric"] == "phase_deg")
