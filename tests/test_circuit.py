from copy import deepcopy

import numpy as np
import pytest
from scipy.integrate import solve_ivp
from scipy.optimize import brentq
from scipy.special import iv

from rf_link_calculator.advanced import circuit
from rf_link_calculator.advanced.api import dispatch
from rf_link_calculator.advanced.benchmark import compare, rows_for
from rf_link_calculator.advanced.circuit_examples import circuits, examples
from rf_link_calculator.domain.codec import hashes, project_from_dict


def amplifier():
    return circuits()["NPN放大电路"]


def test_dc_against_independent_scalar_root_and_jacobian():
    spec = amplifier()
    result = circuit.operating_point(spec)
    vt = 1.380649e-23 * 300 / 1.602176634e-19
    vb = brentq(lambda v: (0.72 - v) / 1000 - 1e-15 * np.expm1(v / vt) / 100 + 1e-15, 0.6, 0.72, xtol=1e-15)
    ic = 1e-15 * np.expm1(vb / vt)
    vc = (5 / 1000 - ic - 2e-15) / (1 / 1000 + 1 / 10000)
    nodes = {r["node"]: r["voltage_v"] for r in result["nodes"]}
    assert nodes["b"] == pytest.approx(vb, abs=1e-12)
    assert nodes["c"] == pytest.approx(vc, abs=3e-12)
    c = circuit.Circuit(spec)
    v = np.array([nodes[name] for name in c.nodes])
    _, jac = c.device(v)
    for j in range(c.size):
        h = np.eye(c.size)[j] * 1e-7
        finite_difference = (c.device(v + h)[0] - c.device(v - h)[0]) / (2e-7)
        np.testing.assert_allclose(jac[0, :, j], finite_difference[0], rtol=1e-8, atol=1e-12)


def test_harmonic_balance_against_independent_time_integration():
    spec = amplifier()
    report = circuit.harmonic_balance(spec, 1e6, 12)
    vt = 1.380649e-23 * 300 / 1.602176634e-19
    cap = np.array([[22e-12, -2e-12], [-2e-12, 2e-12]])

    def rhs(t, x):
        vb, vc = x
        forward = 1e-15 * np.expm1(vb / vt)
        reverse = 1e-15 * np.expm1((vb - vc) / vt)
        currents = [
            (0.72 + 0.01 * np.cos(2 * np.pi * 1e6 * t) - vb) / 1000 - forward / 100 - reverse,
            (5 - vc) / 1000 - vc / 10000 - forward + 2 * reverse,
        ]
        return np.linalg.solve(cap, currents)

    result = solve_ivp(
        rhs,
        [0, 20e-6],
        [0.711, 3.741],
        method="DOP853",
        rtol=2e-10,
        atol=1e-12,
        dense_output=True,
        max_step=2e-8,
        first_step=1e-10,
    )
    assert result.success
    grid = 19e-6 + np.arange(4096) / 4096 / 1e6
    fft = np.fft.rfft(result.sol(grid)[1]) / 4096
    ours = np.array([complex(p["voltage_real_v"], p["voltage_imag_v"]) for p in report["points"]])
    np.testing.assert_allclose(ours, fft[:13], atol=3e-10)
    assert circuit.harmonic_convergence(spec, 1e6, 8)["convergence"]["status"] == "pass"
    assert circuit.harmonic_convergence(spec, 1e6, 1)["convergence"]["status"] == "fail"


def test_passive_noise_lorentzian_and_stability():
    spec = circuits()["RC热噪声"]
    result = circuit.periodic_noise(spec, 1e6, 4, 1e4, 6)
    for p in result["points"]:
        expected = 4 * 1.380649e-23 * 300 * 1000 / (1 + (2 * np.pi * p["frequency_hz"] * 1000 * 1e-9) ** 2)
        assert p["voltage_noise_v2_hz"] == pytest.approx(expected, rel=1e-12, abs=1e-35)
    assert result["stability"]["spectral_radius"] == pytest.approx(np.exp(-1), rel=1e-6)
    assert circuit.noise_convergence(spec, 1e6, 4, 1e4, 4)["convergence"]["status"] == "pass"


def test_dc_transistor_noise_against_independent_hybrid_pi():
    spec = amplifier()
    spec["components"][1]["tones"] = []
    result = circuit.periodic_noise(spec, 1e6, 4, 1e4, 4)
    vt, kt, q = 1.380649e-23 * 300 / 1.602176634e-19, 1.380649e-23 * 300, 1.602176634e-19
    vb = brentq(lambda v: (0.72 - v) / 1000 - 1e-15 * np.expm1(v / vt) / 100, 0.6, 0.72, xtol=1e-15)
    ic = 1e-15 * np.expm1(vb / vt)
    gm = (ic + 1e-15) / vt
    g = np.array([[0.001 + gm / 100, 0], [gm, 0.0011]])
    cap = np.array([[22e-12, -2e-12], [-2e-12, 2e-12]])
    noise = np.diag([4 * kt / 1000 + 2 * q * ic / 100, 4 * kt * (1 / 1000 + 1 / 10000) + 2 * q * ic])
    for p in result["points"]:
        transfer = np.linalg.inv(g + 2j * np.pi * p["frequency_hz"] * cap)[1]
        expected = float((transfer @ noise @ transfer.conj()).real)
        assert p["voltage_noise_v2_hz"] == pytest.approx(expected, rel=1e-9, abs=1e-30)
        assert sum(r["voltage_noise_v2_hz"] for r in p["contributions"]) == pytest.approx(
            expected, rel=1e-9, abs=1e-30
        )


def test_pumped_shot_noise_bessel_and_cyclic_covariance():
    spec = amplifier()
    spec["components"] = [e for e in spec["components"] if e["id"] != "RB"]
    spec["components"][1]["p"] = "b"
    spec["components"][1]["dc_v"] = 0.69
    spec["models"]["NPN1"]["cbe_f"] = spec["models"]["NPN1"]["cbc_f"] = 0
    result = circuit.periodic_noise(spec, 1e6, 12, 1e4, 8)
    vt, kt, q = 1.380649e-23 * 300 / 1.602176634e-19, 1.380649e-23 * 300, 1.602176634e-19
    mean_i = 1e-15 * (np.exp(0.69 / vt) * iv(0, 0.01 / vt) - 1)
    expected = (4 * kt * 0.0011 + 2 * q * mean_i) / 0.0011**2
    for p in result["points"]:
        assert p["voltage_noise_v2_hz"] == pytest.approx(expected, rel=1e-9, abs=1e-30)
    assert result["max_source_cyclic_ratio"] > 0.1


def test_pumped_noise_matches_independent_instantaneous_noise_gain_integral():
    spec = amplifier()
    spec["models"]["NPN1"]["cbe_f"] = spec["models"]["NPN1"]["cbc_f"] = 0
    spec["components"][1]["tones"][0]["peak_v"] = 0.025
    result = circuit.periodic_noise(spec, 1e6, 16, 1e4, 16)
    vt, kt, q = 1.380649e-23 * 300 / 1.602176634e-19, 1.380649e-23 * 300, 1.602176634e-19
    source = 0.72 + 0.025 * np.cos(2 * np.pi * np.arange(2048) / 2048)
    vb = np.array(
        [
            brentq(lambda v: (s - v) / 1000 - 1e-15 * np.expm1(v / vt) / 100, 0.6, 0.8, xtol=1e-15)
            for s in source
        ]
    )
    current = 1e-15 * np.expm1(vb / vt)
    gm = (current + 1e-15) / vt
    gain_b = -gm / (0.0011 * (0.001 + gm / 100))
    expected = np.mean(
        gain_b**2 * (4 * kt / 1000 + 2 * q * current / 100) + (4 * kt * 0.0011 + 2 * q * current) / 0.0011**2
    )
    center = next(p for p in result["points"] if p["frequency_hz"] == 1e4)
    assert center["voltage_noise_v2_hz"] == pytest.approx(expected, rel=2e-8, abs=1e-30)
    # A frozen DC approximation differs from the actual pumped result.
    cold = deepcopy(spec)
    cold["components"][1]["tones"] = []
    dc = circuit.periodic_noise(cold, 1e6, 4, 1e4, 4)
    dc_center = next(p for p in dc["points"] if p["frequency_hz"] == 1e4)
    assert abs(center["noise_dbm_hz"] - dc_center["noise_dbm_hz"]) > 0.01


def test_reject_invalid_circuits_and_unsupported_noise_regions():
    spec = amplifier()
    for mutate in (
        lambda s: s["models"]["NPN1"].update(vaf=100),
        lambda s: s["models"]["NPN1"].update(is_a=True),
        lambda s: s["components"].append(deepcopy(s["components"][0])),
        lambda s: s.update(output_resistor="Q1"),
        lambda s: s["components"][0].update(n="b"),
    ):
        broken = deepcopy(spec)
        mutate(broken)
        with pytest.raises(ValueError):
            circuit.validate(broken)
    spec["components"][0]["dc_v"] = 0.5
    with pytest.raises(ValueError, match="正向放大"):
        circuit.periodic_noise(spec, 1e6, 8, 1e4, 8)
    spec = amplifier()
    spec["models"]["NPN1"]["max_current_a"] = 1e-5
    with pytest.raises(ValueError, match="电流"):
        circuit.harmonic_balance(spec, 1e6, 8)
    for value in (True, 0, 33, 8.5):
        with pytest.raises(ValueError):
            circuit.harmonic_balance(amplifier(), 1e6, value)
    with pytest.raises(ValueError):
        circuit.periodic_noise(amplifier(), 1e6, 8, 6e5, 8)


def test_schema_api_hash_benchmark_and_spice_export():
    import json

    for p in examples().values():
        assert project_from_dict(p.to_dict()) == p
        assert dispatch("circuit-dc", p, {})["solver"]["status"] == "converged"
    p = examples()["NPN放大电路"]
    changed = deepcopy(p.to_dict())
    changed["lab"]["circuit"]["components"][2]["value"] *= 2
    assert hashes(project_from_dict(changed))[1] != hashes(p)[1]
    analysis = {
        "kind": "circuit-noise",
        "parameters": {"fundamental_hz": 1e6, "harmonics": 4, "sidebands": 4, "offset_hz": 1e4},
    }
    rows = rows_for(p, analysis)
    ref = {
        "kind": "reference",
        "rows": rows,
        "metadata": {
            "source": "synthetic",
            "software": "self",
            "version": "1",
            "solver": "test",
            "analysis": analysis,
            "calculation_hash": hashes(p)[1],
        },
    }
    assert compare(p, ref)["status"] == "internal_only"
    density = next(row for row in ref["rows"] if row["unit"] == "V2/Hz")
    density["value"] = str(float(density["value"]) * 2)
    assert any(r["comparison"] == "fail" for r in compare(p, ref)["rows"])
    assert dispatch("circuit-import", p, {"text": json.dumps(p.lab["circuit"])})["project"] == p.to_dict()
    dc = deepcopy(p.lab["circuit"])
    dc["components"][1]["tones"] = []
    text = circuit.spice_netlist(dc)
    assert ".model NPN1 NPN" in text and "C6bc" in text
    transient = circuit.spice_netlist(p.lab["circuit"], 1e6)
    assert "B2 in 0 V=" in transient and ".tran" in transient


def test_floating_nodes_and_unstable_periodic_solution_are_rejected():
    floating = circuits()["RC热噪声"]
    floating["components"].append({"id": "CF", "type": "capacitor", "p": "floating", "n": "0", "value": 1e-9})
    with pytest.raises(ValueError, match="奇异"):
        circuit.operating_point(floating)
    models = amplifier()["models"]
    models["NPN1"].update(cbe_f=1e-9, cbc_f=0)
    unstable = {
        "version": 1,
        "temperature_k": 300,
        "models": models,
        "output_resistor": "R1",
        "components": [
            {"id": "V1", "type": "voltage", "p": "vcc", "n": "0", "dc_v": 5.0, "tones": []},
            {"id": "R1", "type": "resistor", "p": "c1", "n": "vcc", "value": 1000.0},
            {"id": "R2", "type": "resistor", "p": "c2", "n": "vcc", "value": 1000.0},
            {"id": "Q1", "type": "npn", "b": "c2", "c": "c1", "e": "0", "model": "NPN1"},
            {"id": "Q2", "type": "npn", "b": "c1", "c": "c2", "e": "0", "model": "NPN1"},
        ],
    }
    with pytest.raises(ValueError, match="不稳定"):
        circuit.periodic_noise(unstable, 1e8, 2, 1e5, 2)


def test_observation_band_is_fixed_during_noise_refinement_and_failures_remain_visible():
    result = circuit.noise_convergence(amplifier(), 1e6, 8, 1e4, 8, 1)
    assert result["convergence"]["status"] == "pass"
    assert sorted(p["frequency_hz"] for p in result["points"]) == [1e4, 0.99e6, 1.01e6]
    # At the mixing cutoff the omitted neighbouring sideband is measurable.
    edge = circuit.noise_convergence(amplifier(), 1e6, 4, 1e4, 4, 4)
    assert edge["convergence"]["status"] == "fail"
    with pytest.raises(ValueError, match="谐波截断"):
        circuit.noise_convergence(amplifier(), 1e6, 1, 1e4, 8)
