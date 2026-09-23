import shlex
import shutil
import subprocess
from dataclasses import dataclass, field

from rf_link_calculator.domain.models import Project, ResultBundle
from rf_link_calculator.persistence.json_io import hashes

from .symbols import SYMBOL_VERSION, Primitive, symbol


def wrap(text: str, width: int = 30) -> list[str]:
    lines, current, size = [], "", 0
    for ch in text:
        units = 2 if ord(ch) > 255 else 1
        if ch == "\n" or size + units > width:
            lines.append(current)
            current, size = "", 0
        if ch != "\n":
            current += ch
            size += units
    if current:
        lines.append(current)
    return lines or [""]


@dataclass
class Node:
    id: str
    stage_id: str | None
    kind: str
    lines: list[str]
    x: float = 0
    y: float = 240
    w: float = 240
    h: float = 160
    primitives: list[Primitive] = field(default_factory=list)

    def port(self, port: str) -> tuple[float, float]:
        return {
            "in": (self.x, self.y + 50),
            "out": (self.x + self.w, self.y + 50),
            "lo": (self.x + 120, self.y),
            "source": (self.x + 120, self.y + 80),
        }[port]


@dataclass
class Edge:
    id: str
    source: str
    target: str
    source_port: str = "out"
    target_port: str = "in"
    label: str = ""
    auxiliary: bool = False


@dataclass
class DiagramScene:
    title: str
    nodes: list[Node]
    edges: list[Edge]
    width: float
    height: float
    metadata: dict
    warnings: list[str]


def fmt(value, unit):
    return f"{value:.3f} {unit}" if value is not None else "未知"


def parameter_label(metric):
    return "显式理想（无有限值）" if metric.status == "ideal" else fmt(metric.value, metric.unit)


def build_scene(
    project: Project, result: ResultBundle, stage_ids=None, segment_label="", prefer_graphviz=True
) -> DiagramScene:
    if hashes(project)[0] != result.project_hash:
        raise ValueError("输入与结果不一致，禁止组合新旧图表")
    lookup = {r.stage_id: r for r in result.stages}
    active = [s for s in project.stages if s.enabled and (stage_ids is None or s.id in stage_ids)]
    nodes = [Node("input", None, "input", [segment_label + "入口 / 50 Ω"])]
    for i, s in enumerate(active):
        r = lookup[s.id].metrics
        labels = wrap(s.name) + [
            f"G {fmt(r['stage_gain_db'].value, 'dB')} / NF {fmt(r['stage_nf_db'].value, 'dB')}",
            "IIP3 " + parameter_label(r["stage_iip3_dbm"]),
            "输入P1 " + parameter_label(r["stage_ip1_dbm"]),
            "输出 " + fmt(r["linear_output_dbm"].value, "dBm") + "（线性）",
        ]
        nodes.append(Node("stage-" + s.id, s.id, s.type, labels, h=105 + 20 * len(labels)))
    nodes.append(Node("output", None, "output", [segment_label + "出口 / 50 Ω"]))
    for i, n in enumerate(nodes):
        n.x = 40 + i * 430
        n.primitives = symbol(n.kind)
    edges = []
    for i, (src, dst) in enumerate(zip(nodes, nodes[1:])):
        if src.stage_id:
            m = lookup[src.stage_id].metrics
            freq, power = m["frequency_output_hz"].value, m["linear_output_dbm"].value
        elif active:
            m = lookup[active[0].id].metrics
            freq, power = m["frequency_input_hz"].value, m["linear_input_dbm"].value
        else:
            freq, power = project.analysis.source_frequency_hz, project.analysis.input_power_dbm
        label = f"{freq / 1e6:.3f} MHz\n" if freq is not None else "频率未知\n"
        edges.append(Edge(f"main-{i}", src.id, dst.id, label=label + fmt(power, "dBm") + " 线性"))
    layout, warnings = "deterministic-linear", []
    dot = shutil.which("dot")
    if dot and prefer_graphviz:
        # Generated identifiers only: no user text is interpolated into a command or DOT source.
        code = [
            'digraph G { graph [rankdir=LR,nodesep=0.5,ranksep=1.9]; node [shape=box,fixedsize=true,label=""];'
        ]
        code += [f"n{i} [width={n.w / 96:.6f},height={n.h / 96:.6f}];" for i, n in enumerate(nodes)]
        code += [f"n{i} -> n{i + 1};" for i in range(len(nodes) - 1)] + ["}"]
        try:
            proc = subprocess.run(
                [dot, "-Tplain"], input="\n".join(code), capture_output=True, text=True, timeout=5, check=True
            )
            found = {}
            for line in proc.stdout.splitlines():
                parts = shlex.split(line)
                if parts and parts[0] == "node":
                    found[int(parts[1][1:])] = float(parts[2]) * 96
            if len(found) != len(nodes):
                raise ValueError("Graphviz未返回完整节点坐标")
            for i, n in enumerate(nodes):
                n.x = found[i] - n.w / 2 + 40
            layout = "graphviz-dot-plain"
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            warnings.append("Graphviz布局失败，采用确定性串行布局：" + str(exc)[:180])
    else:
        warnings.append("Graphviz未启用，采用可缩放宽图／分段串行布局")
    stage_nodes = {n.stage_id: n for n in nodes if n.stage_id}
    for s in active:
        if s.type == "mixer" and s.mixer:
            main = stage_nodes[s.id]
            lo = Node(
                "lo-" + s.id,
                None,
                "lo",
                [
                    "LO " + fmt(s.mixer.lo_frequency_hz / 1e6, "MHz"),
                    "驱动 " + fmt(s.mixer.lo_power_dbm, "dBm"),
                ],
                x=main.x,
                y=30,
                h=140,
                primitives=symbol("lo"),
            )
            nodes.append(lo)
            edges.append(Edge("aux-" + s.id, lo.id, main.id, "source", "lo", "LO辅助端口", True))
    width = max(n.x + n.w for n in nodes) + 40
    height = max(n.y + n.h for n in nodes) + 110
    return DiagramScene(
        project.link_name + (" / " + segment_label if segment_label else ""),
        nodes,
        edges,
        width,
        height,
        {
            "calculation_hash": result.calculation_hash,
            "project_hash": result.project_hash,
            "layout": layout,
            "formula_version": result.formula_version,
            "symbol_version": SYMBOL_VERSION,
        },
        warnings,
    )


def segmented_scenes(project: Project, result: ResultBundle, per_page: int = 10) -> list[DiagramScene]:
    if not 1 <= per_page <= 50:
        raise ValueError("分段大小应在1～50级")
    ids = [s.id for s in project.stages if s.enabled]
    return [
        build_scene(
            project,
            result,
            ids[i : i + per_page],
            f"段{i // per_page + 1}（级{i + 1}～{min(i + per_page, len(ids))}）",
        )
        for i in range(0, len(ids), per_page)
    ] or [build_scene(project, result)]
