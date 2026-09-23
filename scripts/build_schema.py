"""Development-time JSON schema generator; committed schema is packaged with the app."""

import json
from pathlib import Path

from rf_link_calculator.domain.labels import TYPES


def obj(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def num(lo=-1e6, hi=1e6, nullable=False):
    return {"type": ["number", "null"] if nullable else "number", "minimum": lo, "maximum": hi}


def enum(*values):
    return {"enum": list(values)}


def main():
    text = {"type": "string", "maxLength": 4096, "pattern": r"^[^\x00-\x08\x0b\x0c\x0e-\x1f]*$"}
    name = dict(text, minLength=1, maxLength=512)
    boolean = {"type": "boolean"}
    positive = num(1e-12, 1e15)
    noise = obj(
        {"mode": enum("unknown", "ideal", "manual", "passive_thermal"), "value_db": num(0, 300, True)}
    )

    def power(refs):
        return obj(
            {
                "mode": enum("unknown", "ideal", "finite"),
                "reference": enum(*refs),
                "value_dbm": num(-1000, 1000, True),
            }
        )

    source = obj(
        {
            "kind": text,
            "note": text,
            "url": text,
            "version": text,
            "bias": text,
            "specification": enum("typ", "min", "max", "assumed", ""),
            "test_frequency_hz": positive,
            "temperature_k": num(1e-9, 1e6),
            "conditions_mismatch": boolean,
        }
    )
    mixer = obj(
        {
            "lo_frequency_hz": positive,
            "relation": enum("sum", "difference"),
            "noise_convention": enum("SSB", "DSB", "unknown"),
            "noise_compatible": boolean,
            "lo_power_dbm": num(-1000, 1000, True),
        }
    )
    stage = obj(
        {
            "id": name,
            "order": {"type": "integer", "minimum": 1, "maximum": 200},
            "enabled": boolean,
            "name": name,
            "type": enum(*TYPES),
            "gain_db": num(-300, 300, True),
            "noise": noise,
            "ip3": power(("input", "output")),
            "p1db": power(("input", "actual_output", "linear_output")),
            "physical_temperature_k": num(1e-9, 1e6),
            "compression_p": num(1, 10, True),
            "frequency_range_hz": {
                "type": ["array", "null"],
                "items": positive,
                "minItems": 2,
                "maxItems": 2,
            },
            "mixer": {"anyOf": [mixer, {"type": "null"}]},
            "absolute_max_input_dbm": num(-1000, 1000, True),
            "source": source,
            "notes": text,
        },
        ("id", "order", "name"),
    )
    analysis = obj(
        {
            "source_frequency_hz": positive,
            "signal_bandwidth_hz": positive,
            "noise_bandwidth_hz": positive,
            "source_noise_temperature_k": num(1e-9, 1e6),
            "reference_temperature_k": {"const": 290},
            "reference_impedance_ohm": {"const": 50},
            "input_power_dbm": num(-1000, 1000),
            "signal_mode": enum("cw", "two_tone"),
            "required_snr_db": num(-300, 300),
            "implementation_loss_db": num(0, 300),
            "compression_backoff_db": num(0, 300),
            "compression_model": {"const": "p1_anchored_soft_saturation_v1"},
            "default_compression_p": num(1, 10),
            "two_tone": obj(
                {"enabled": boolean, "each_tone_power_dbm": num(-1000, 1000), "spacing_hz": positive}
            ),
        }
    )
    schema = obj(
        {
            "schema_version": {"const": "1.0.0"},
            "project_id": name,
            "project_name": name,
            "link_name": name,
            "link_type": enum("receiver", "transmitter", "upconverter", "downconverter", "other"),
            "analysis": analysis,
            "stages": {"type": "array", "maxItems": 200, "items": stage},
            "assumptions": {"type": "array", "maxItems": 200, "items": text},
            "notes": text,
        }
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    root = Path(__file__).resolve().parents[1]
    for p in (
        root / "schemas/project-v1.schema.json",
        root / "src/rf_link_calculator/project-v1.schema.json",
    ):
        p.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
