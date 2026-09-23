"""Execute an independently installed ngspice; retain inputs, outputs and hashes.

No commercial license or target measurement is inferred from this comparison.
Run: python scripts/verify_circuit_reference.py --ngspice PATH_TO_NGSPICE_CON.EXE
"""

import argparse
import json
import subprocess
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import numpy as np

from rf_link_calculator import __version__
from rf_link_calculator.advanced.circuit import (
    harmonic_balance,
    operating_point,
    periodic_noise,
    spice_netlist,
)
from rf_link_calculator.advanced.circuit_examples import circuits

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "samples/circuit-validation/ngspice"
OPTIONS = ".options gmin=1e-18 reltol=1e-9 abstol=1e-15 vntol=1e-12\n"


def execute(binary, name, netlist, commands):
    path = OUTPUT / (name + ".cir")
    text = (
        netlist.replace(".op\n.end\n", "")
        + OPTIONS
        + ".control\nset numdgt=15\nset wr_singlescale\nset wr_vecnames\n"
        + commands
        + "\nquit\n.endc\n.end\n"
    )
    path.write_text(text, "ascii")
    run = subprocess.run(
        [str(binary), "-b", "-o", name + ".txt", path.name],
        cwd=OUTPUT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if run.returncode or "error" in (OUTPUT / (name + ".txt")).read_text("utf-8", errors="replace").lower():
        raise RuntimeError("ngspice reference failed: " + name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ngspice", required=True, type=Path)
    args = parser.parse_args()
    binary = args.ngspice.resolve(strict=True)
    version = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, check=True, timeout=10
    ).stdout.strip()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = {
        "version": __version__,
        "software": version,
        "binary_sha256": sha256(binary.read_bytes()).hexdigest(),
        "source": "independent",
        "scope": "actual ngspice DC, transient Fourier and stationary noise; pumped noise is NOT compared to ngspice .noise",
        "checks": [],
    }
    dc_template = circuits()["NPN放大电路"]
    dc_template["components"][1]["tones"] = []
    for index, (temperature, bias) in enumerate(
        (t, b) for t in (290.0, 300.0, 320.0) for b in (0.66, 0.70, 0.74)
    ):
        spec = deepcopy(dc_template)
        spec["temperature_k"] = temperature
        spec["components"][1]["dc_v"] = bias
        name = f"dc-{index}"
        execute(binary, name, spice_netlist(spec), f"op\nwrdata {name}.tsv v(b) v(c)")
        reference = np.loadtxt(OUTPUT / (name + ".tsv"), skiprows=1)
        ours = {r["node"]: r["voltage_v"] for r in operating_point(spec)["nodes"]}
        for node, value in zip(("b", "c"), reference[1:]):
            delta = ours[node] - float(value)
            report["checks"].append(
                {
                    "case": name,
                    "temperature_k": temperature,
                    "bias_v": bias,
                    "node": node,
                    "delta_v": delta,
                    "limit_v": 2e-5,
                    "pass": bool(abs(delta) < 2e-5),
                }
            )
    net = spice_netlist(dc_template).replace(
        "V2 in 0 0.72", "V2 in 0 DC 0.72 AC 1 SIN(0.72 0.01 1000000 0 0 90)"
    )
    execute(
        binary,
        "dynamic",
        net,
        "noise v(c) V2 lin 9 10000 4010000\nsetplot noise1\nwrdata noise.tsv onoise_spectrum\ntran 0.25n 20u 19u 0.25n\nlinearize v(c)\nwrdata transient.tsv v(c)",
    )
    transient = np.loadtxt(OUTPUT / "transient.tsv", skiprows=1)
    np.testing.assert_allclose(transient[:, 0], 19e-6 + np.arange(4001) * 0.25e-9, atol=1e-14)
    reference = np.fft.rfft(transient[:-1, 1]) / 4000
    ours = harmonic_balance(circuits()["NPN放大电路"], 1e6, 8)
    for index, point in enumerate(ours["points"][:5]):
        value = complex(point["voltage_real_v"], point["voltage_imag_v"])
        delta = float(20 * np.log10(abs(value / reference[index])))
        report["checks"].append(
            {
                "case": "transient_fourier",
                "harmonic": index,
                "voltage_complex_error_v": float(abs(value - reference[index])),
                "delta_db": delta,
                "limit_db": 0.001,
                "pass": bool(abs(delta) < 0.001),
            }
        )
    stationary = periodic_noise(dc_template, 5e5, 4, 1e4, 8, 8)
    lookup = {p["frequency_hz"]: p["voltage_noise_v2_hz"] for p in stationary["points"]}
    for frequency, amplitude in np.loadtxt(OUTPUT / "noise.tsv", skiprows=1):
        delta = float(10 * np.log10(lookup[frequency] / amplitude**2))
        report["checks"].append(
            {
                "case": "stationary_noise",
                "frequency_hz": float(frequency),
                "delta_db": delta,
                "limit_db": 0.001,
                "pass": bool(abs(delta) < 0.001),
            }
        )
    # Preserve observed thermal-voltage convention as evidence, without changing
    # our SI constants or tuning model parameters to make this comparison pass.
    execute(binary, "constants", spice_netlist(dc_template), "op\nprint @q6[ic] @q6[gm]")
    report["status"] = "passed" if all(r["pass"] for r in report["checks"]) else "failed"
    report["files"] = [
        {"path": str(p.relative_to(ROOT)), "sha256": sha256(p.read_bytes()).hexdigest()}
        for p in sorted(OUTPUT.iterdir())
        if p.is_file()
    ]
    (ROOT / "docs/晶体管外部对标.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "checks": len(report["checks"]),
                "dc_max_delta_v": max(abs(r.get("delta_v", 0)) for r in report["checks"]),
                "max_delta_db": max(abs(r.get("delta_db", 0)) for r in report["checks"]),
            },
            indent=2,
        )
    )
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
