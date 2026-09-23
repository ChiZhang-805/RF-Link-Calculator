import json

from rf_link_calculator.diagnostics import diagnose
from rf_link_calculator.engine import calculate
from rf_link_calculator.examples import examples
from rf_link_calculator.exporters.excel import build_excel

if __name__ == "__main__":
    checks = diagnose()
    p = examples()["接收链路"]
    try:
        raw = build_excel(p, calculate(p))
        checks["Excel生成"] = f"通过；{len(raw)}字节（不代表桌面重算已执行）"
    except Exception as exc:
        checks["Excel生成"] = str(exc)
    print(json.dumps(checks, ensure_ascii=False, indent=2))
