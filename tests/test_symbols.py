from xml.etree import ElementTree as ET

from rf_link_calculator.diagram.scene import build_scene
from rf_link_calculator.diagram.symbols import symbol
from rf_link_calculator.engine import calculate
from rf_link_calculator.examples import examples
from rf_link_calculator.exporters.drawio import render_drawio
from rf_link_calculator.exporters.svg import render_svg


def test_rf_amplifiers_share_single_input_symbol_without_overflowing_text():
    for kind in ("amplifier", "lna", "pa", "vga"):
        shapes = symbol(kind)
        assert shapes == symbol("amplifier")
        assert not any(p.kind == "text" for p in shapes)
        triangle = next(p for p in shapes if p.kind == "polygon")
        xs, ys = zip(*triangle.points)
        assert max(xs) - min(xs) == max(ys) - min(ys)
        assert (min(xs), 50) in shapes[0].points
        assert (max(xs), 50) in shapes[1].points


def test_filter_stop_band_marks_are_distinct_without_changing_ports():
    patterns = set()
    for kind in ("filter_bpf", "filter_lpf", "filter_hpf", "filter_bsf"):
        shapes = symbol(kind)
        assert shapes[0].points[0] == (0, 50)
        assert shapes[1].points[-1] == (240, 50)
        assert len([p for p in shapes if p.kind == "polyline" and len(p.points) > 10]) == 3
        patterns.add(tuple(p.points for p in shapes[6:]))
    assert len(patterns) == 4


def test_mixer_lo_connection_touches_geometry_in_both_vector_formats():
    project = examples()["下变频链路"]
    scene = build_scene(project, calculate(project), prefer_graphviz=False)
    mixer = next(n for n in scene.nodes if n.kind == "mixer")
    lo = next(n for n in scene.nodes if n.kind == "lo")
    assert (120, 0) in mixer.primitives[-1].points
    assert lo.port("source") == (lo.x + 120, lo.y + 80)
    xml = ET.fromstring(render_drawio(scene))
    cells = {c.attrib["id"]: c for c in xml.findall(".//mxCell")}
    assert cells["aux-" + mixer.stage_id].attrib["target"] == mixer.id + "-port-lo"
    assert cells["aux-" + mixer.stage_id].attrib["source"] == lo.id + "-port-source"
    assert mixer.id + "-port-source" not in cells
    assert lo.id + "-port-in" not in cells
    assert "group;" in cells[mixer.id].attrib["style"]
    svg = ET.fromstring(render_svg(scene))
    assert any(e.attrib.get("id") == mixer.id for e in svg.iter())
    assert b"AMPLIFIER" not in render_svg(scene)
