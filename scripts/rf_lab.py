"""Replay an advanced request from explicit project/request files outside the UI."""

import argparse
import json
from pathlib import Path

from rf_link_calculator.advanced.api import dispatch
from rf_link_calculator.engine import calculate
from rf_link_calculator.persistence.json_io import atomic_write, loads


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument(
        "request",
        type=Path,
        help='Object with action and parameters, e.g. {"action":"sweep","parameters":{"low":90000000,"high":110000000}}',
    )
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    project = loads(args.project.read_bytes())
    request = json.loads(args.request.read_text("utf-8-sig"))
    action = request["action"]
    result = (
        calculate(project).to_dict()
        if action == "calculate"
        else dispatch(action, project, request.get("parameters", {}))
    )
    raw = (
        result[0]
        if isinstance(result, tuple)
        else json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    )
    atomic_write(args.output, raw)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
