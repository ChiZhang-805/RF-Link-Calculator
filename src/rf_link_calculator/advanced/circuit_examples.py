"""Synthetic intrinsic transistor and passive thermal-noise circuits."""

from copy import deepcopy


def circuits():
    amplifier = {
        "version": 1,
        "temperature_k": 300,
        "metadata": {"source": "synthetic", "model": "intrinsic Ebers-Moll, constant junction capacitances"},
        "models": {
            "NPN1": {
                "type": "npn_ebers_moll",
                "is_a": 1e-15,
                "bf": 100.0,
                "br": 1.0,
                "cbe_f": 20e-12,
                "cbc_f": 2e-12,
                "max_current_a": 0.02,
                "max_vbe_v": 0.9,
                "max_vce_v": 12.0,
            }
        },
        "components": [
            {"id": "VCC", "type": "voltage", "p": "vcc", "n": "0", "dc_v": 5.0, "tones": []},
            {
                "id": "VIN",
                "type": "voltage",
                "p": "in",
                "n": "0",
                "dc_v": 0.72,
                "tones": [{"harmonic": 1, "peak_v": 0.01, "phase_deg": 0.0}],
            },
            {"id": "RB", "type": "resistor", "p": "in", "n": "b", "value": 1000.0},
            {"id": "RC", "type": "resistor", "p": "vcc", "n": "c", "value": 1000.0},
            {"id": "RL", "type": "resistor", "p": "c", "n": "0", "value": 10000.0},
            {"id": "Q1", "type": "npn", "b": "b", "c": "c", "e": "0", "model": "NPN1"},
        ],
        "output_resistor": "RL",
    }
    rc = {
        "version": 1,
        "temperature_k": 300,
        "models": {},
        "metadata": {"source": "synthetic"},
        "components": [
            {"id": "R1", "type": "resistor", "p": "out", "n": "0", "value": 1000.0},
            {"id": "C1", "type": "capacitor", "p": "out", "n": "0", "value": 1e-9},
        ],
        "output_resistor": "R1",
    }
    return {"NPN放大电路": amplifier, "RC热噪声": rc}


def examples():
    from rf_link_calculator.domain.models import Project

    return {
        name: Project(
            schema_version="2.0.0",
            project_id=name,
            project_name=name,
            link_name=name,
            stages=(),
            lab={
                "models": {},
                "circuit": deepcopy(circuit),
                "settings": {
                    "circuit_base_hz": 1e6,
                    "circuit_harmonics": 8,
                    "circuit_sidebands": 8,
                    "circuit_offset_hz": 1e4,
                },
            },
        )
        for name, circuit in circuits().items()
    }
