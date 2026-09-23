from xml.etree.ElementTree import Element, SubElement, tostring

from rf_link_calculator.diagram.scene import DiagramScene


def render_svg(scene: DiagramScene) -> bytes:
    root = Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "width": str(scene.width),
            "height": str(scene.height),
            "viewBox": f"0 0 {scene.width} {scene.height}",
            "role": "img",
        },
    )
    SubElement(root, "title").text = scene.title
    SubElement(root, "desc").text = str(scene.metadata) + "; ".join(scene.warnings)
    defs = SubElement(root, "defs")
    marker = SubElement(
        defs,
        "marker",
        {
            "id": "arrow",
            "viewBox": "0 0 10 10",
            "refX": "9",
            "refY": "5",
            "markerWidth": "7",
            "markerHeight": "7",
            "orient": "auto-start-reverse",
        },
    )
    SubElement(marker, "path", {"d": "M0,0 L10,5 L0,10 z", "fill": "#202830"})
    SubElement(root, "rect", {"width": "100%", "height": "100%", "fill": "#ffffff"})
    group = SubElement(
        root,
        "g",
        {
            "font-family": "Microsoft YaHei,Noto Sans CJK SC,Arial,sans-serif",
            "font-size": "13",
            "fill": "#18354c",
        },
    )
    lookup = {n.id: n for n in scene.nodes}
    for e in scene.edges:
        x1, y1 = lookup[e.source].port(e.source_port)
        x2, y2 = lookup[e.target].port(e.target_port)
        mx = (x1 + x2) / 2
        SubElement(
            group,
            "path",
            {
                "d": f"M{x1},{y1} L{mx},{y1} L{mx},{y2} L{x2},{y2}",
                "stroke": "#202830",
                "fill": "none",
                "stroke-width": "2",
                "marker-end": "url(#arrow)",
            },
        )
        tx = mx + 12 if e.auxiliary else mx
        ty = (y1 + y2) / 2 if e.auxiliary else y1 - 32
        text = SubElement(
            group, "text", {"x": str(tx), "y": str(ty), "text-anchor": "start" if e.auxiliary else "middle"}
        )
        for i, line in enumerate(e.label.splitlines()):
            SubElement(text, "tspan", {"x": str(tx), "dy": "0" if i == 0 else "18"}).text = line
    for n in scene.nodes:
        g = SubElement(
            group,
            "g",
            {"id": n.id, "data-stage-id": n.stage_id or "", "transform": f"translate({n.x},{n.y})"},
        )
        for p in n.primitives:
            attrs = {"stroke": "#202830", "stroke-width": "2", "fill": "#ffffff"}
            if p.kind in ("rect", "ellipse"):
                (x, y), (w, h) = p.points
                if p.kind == "rect":
                    attrs.update(x=str(x), y=str(y), width=str(w), height=str(h))
                else:
                    attrs.update(cx=str(x + w / 2), cy=str(y + h / 2), rx=str(w / 2), ry=str(h / 2))
                SubElement(g, p.kind, attrs)
            elif p.kind in ("polyline", "polygon"):
                attrs["points"] = " ".join(f"{x},{y}" for x, y in p.points)
                if p.kind == "polyline":
                    attrs["fill"] = "none"
                SubElement(g, p.kind, attrs)
            else:
                SubElement(g, "text", {"x": str(p.points[0][0]), "y": str(p.points[0][1])}).text = p.text
        text = SubElement(
            g, "text", {"x": "120", "y": "-10" if n.kind == "lo" else "115", "text-anchor": "middle"}
        )
        for i, line in enumerate(n.lines):
            SubElement(text, "tspan", {"x": "120", "dy": "0" if i == 0 else "20"}).text = line
    SubElement(group, "text", {"x": "40", "y": str(scene.height - 50)}).text = (
        "50 Ω匹配标量预算｜功率为线性预算｜P1为独立模型估算｜布局: " + scene.metadata["layout"]
    )
    SubElement(group, "text", {"x": "40", "y": str(scene.height - 26)}).text = (
        "计算散列: " + scene.metadata["calculation_hash"]
    )
    return tostring(root, encoding="utf-8", xml_declaration=True)
