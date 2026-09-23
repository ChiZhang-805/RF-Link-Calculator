"""Native uncompressed mxfile: groups, primitive vertices, child ports, glued edges."""

from xml.etree.ElementTree import Element, SubElement, tostring

from rf_link_calculator.diagram.scene import DiagramScene


def render_drawio(scenes: DiagramScene | list[DiagramScene]) -> bytes:
    if isinstance(scenes, DiagramScene):
        scenes = [scenes]
    mx = Element("mxfile", {"host": "app.diagrams.net", "agent": "RF-Link-Calculator", "version": "1.0"})
    for page, scene in enumerate(scenes, 1):
        diagram = SubElement(mx, "diagram", {"id": f"page-{page}", "name": scene.title})
        model = SubElement(
            diagram,
            "mxGraphModel",
            {
                "grid": "1",
                "page": "0",
                "pageWidth": str(scene.width),
                "pageHeight": str(scene.height),
                "math": "0",
                "rfCalculationHash": scene.metadata["calculation_hash"],
            },
        )
        root = SubElement(model, "root")
        SubElement(root, "mxCell", {"id": "0"})
        SubElement(root, "mxCell", {"id": "1", "parent": "0"})

        def vertex(id, parent, x, y, w, h, style, value=""):
            c = SubElement(
                root, "mxCell", {"id": id, "parent": parent, "vertex": "1", "value": value, "style": style}
            )
            SubElement(
                c,
                "mxGeometry",
                {"x": str(x), "y": str(y), "width": str(w), "height": str(h), "as": "geometry"},
            )
            return c

        base = "html=0;part=1;connectable=0;strokeColor=#202830;fillColor=#ffffff;strokeWidth=2;fontFamily=Microsoft YaHei;fontSize=13;"
        for n in scene.nodes:
            vertex(n.id, "1", n.x, n.y, n.w, n.h, "group;collapsible=0;", "")
            for i, p in enumerate(n.primitives):
                id = n.id + f"-shape-{i}"
                if p.kind in ("rect", "ellipse"):
                    (x, y), (w, h) = p.points
                    vertex(id, n.id, x, y, w, h, base + ("ellipse;" if p.kind == "ellipse" else "rounded=0;"))
                elif p.kind == "polygon" and len(p.points) == 3:
                    xs, ys = zip(*p.points)
                    vertex(
                        id,
                        n.id,
                        min(xs),
                        min(ys),
                        max(xs) - min(xs),
                        max(ys) - min(ys),
                        base + "triangle;direction=east;",
                    )
                elif p.kind in ("polyline", "polygon"):
                    pts = list(p.points)
                    if p.kind == "polygon":
                        pts.append(pts[0])
                    c = SubElement(
                        root,
                        "mxCell",
                        {
                            "id": id,
                            "parent": n.id,
                            "edge": "1",
                            "style": "html=0;part=1;connectable=0;endArrow=none;strokeColor=#202830;strokeWidth=2;",
                        },
                    )
                    geo = SubElement(c, "mxGeometry", {"relative": "1", "as": "geometry"})
                    for pt, role in ((pts[0], "sourcePoint"), (pts[-1], "targetPoint")):
                        SubElement(geo, "mxPoint", {"x": str(pt[0]), "y": str(pt[1]), "as": role})
                    arr = SubElement(geo, "Array", {"as": "points"})
                    for x, y in pts[1:-1]:
                        SubElement(arr, "mxPoint", {"x": str(x), "y": str(y)})
                else:
                    vertex(
                        id,
                        n.id,
                        p.points[0][0],
                        p.points[0][1] - 14,
                        100,
                        20,
                        base + "text;strokeColor=none;fillColor=none;align=left;",
                        p.text,
                    )
            vertex(
                n.id + "-label",
                n.id,
                0,
                -30 if n.kind == "lo" else 100,
                n.w,
                20 * len(n.lines) + 10,
                base + "text;strokeColor=none;fillColor=none;whiteSpace=wrap;align=center;verticalAlign=top;",
                "\n".join(n.lines),
            )
            ports = (
                ("source",)
                if n.kind == "lo"
                else (("in", "out", "lo") if n.kind == "mixer" else ("in", "out"))
            )
            for port in ports:
                x, y = n.port(port)
                vertex(
                    n.id + "-port-" + port,
                    n.id,
                    x - n.x - 2,
                    y - n.y - 2,
                    4,
                    4,
                    "ellipse;part=1;resizable=0;movable=0;fillColor=none;strokeColor=none;",
                )
        for e in scene.edges:
            c = SubElement(
                root,
                "mxCell",
                {
                    "id": e.id,
                    "parent": "1",
                    "edge": "1",
                    "source": e.source + "-port-" + e.source_port,
                    "target": e.target + "-port-" + e.target_port,
                    "value": e.label,
                    "style": "edgeStyle=orthogonalEdgeStyle;html=0;rounded=0;endArrow=block;strokeColor=#202830;strokeWidth=2;fontSize=12;labelBackgroundColor=#ffffff;",
                },
            )
            SubElement(c, "mxGeometry", {"relative": "1", "as": "geometry"})
        vertex(
            "legend",
            "1",
            40,
            scene.height - 70,
            scene.width - 80,
            60,
            "text;html=0;align=left;strokeColor=none;fillColor=none;",
            "50 Ω匹配标量预算；节点与文字可编辑；图形修改不回写计算。\n计算散列: "
            + scene.metadata["calculation_hash"],
        )
    return tostring(mx, encoding="utf-8", xml_declaration=True)
