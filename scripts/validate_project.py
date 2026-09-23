import argparse
import json
from pathlib import Path

from rf_link_calculator.engine import calculate
from rf_link_calculator.persistence.json_io import loads

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="验证项目并计算全部指标")
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    r = calculate(loads(args.project.read_bytes()))
    print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2, allow_nan=False))
