import argparse
from pathlib import Path

from rf_link_calculator.engine import calculate
from rf_link_calculator.examples import examples
from rf_link_calculator.exporters.bundle import export_bundle, save_bundle
from rf_link_calculator.persistence.json_io import dumps

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--example", default="接收链路")
    parser.add_argument("--output", type=Path, default=Path("outputs"))
    parser.add_argument("--write-examples", action="store_true")
    args = parser.parse_args()
    all_examples = examples()
    if args.write_examples:
        for name, p in all_examples.items():
            Path("examples", name + ".json").write_text(dumps(p), encoding="utf-8")
    p = all_examples[args.example]
    raw, manifest = export_bundle(p, calculate(p))
    print(save_bundle(raw, args.output, p))
    print(manifest["status"])
