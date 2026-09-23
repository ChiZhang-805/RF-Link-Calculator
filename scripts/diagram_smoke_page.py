"""Local test-only page with synthetic diagrams, not part of the application's network behavior."""

import base64
import html
import urllib.parse
import zlib
from pathlib import Path

from rf_link_calculator.diagram.scene import build_scene
from rf_link_calculator.engine import calculate
from rf_link_calculator.examples import examples
from rf_link_calculator.exporters.drawio import render_drawio
from rf_link_calculator.exporters.svg import render_svg

root = Path("artifacts/diagram-check")
root.mkdir(parents=True, exist_ok=True)
p = examples()["下变频链路"]
scene = build_scene(p, calculate(p))
xml = render_drawio(scene)
(root / "下变频.drawio").write_bytes(xml)
(root / "下变频.svg").write_bytes(render_svg(scene))
encoded = urllib.parse.quote(xml.decode("utf-8"), safe="~()*!.'-").encode("ascii")
packer = zlib.compressobj(9, zlib.DEFLATED, -15)
compressed = packer.compress(encoded) + packer.flush()
url = "https://app.diagrams.net/?splash=0&lang=zh#R" + urllib.parse.quote(
    base64.b64encode(compressed).decode("ascii"), safe=""
)
(root / "index.html").write_text(
    '<!doctype html><meta charset="utf-8"><title>构造数据框图验收</title><h1>构造数据框图验收</h1><p>此页只包含程序生成的演示数据，不含用户器件资料。</p><a href="'
    + html.escape(url, quote=True)
    + '">在官方draw.io编辑器检查原生图形</a><p><a href="下变频.svg">查看SVG宽图</a></p><object type="image/svg+xml" data="下变频.svg" width="100%"></object>',
    encoding="utf-8",
)
print(root.resolve())
