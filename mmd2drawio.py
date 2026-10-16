"""Convert a Mermaid flowchart into an editable draw.io diagram.

    python mmd2drawio.py <diagram.mmd> <diagram.drawio>

Mermaid stays the source of truth. This renders it with the Mermaid CLI (`mmdc`)
to SVG, takes the layout from there (node centres and sizes, cluster boxes, edge
routes from `data-points`) and the meaning from the .mmd source (labels,
shapes, dashed, bidirectional and undirected edges), and writes native draw.io
cells: clusters become containers holding their nodes, so the result can be
edited in draw.io without falling apart.

Handles the flowchart subset these docs use: `subgraph id["label"] ... end`,
nodes `A["x"]`, `A[("x")]` (cylinder), `A(["x"])` (stadium), and edges
`-->`, `-.->`, `---`, `<-->`, each optionally labelled `|"x"|`, including
chains `A --> B --> C`.
"""
import base64
import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

NODE_RE = re.compile(r'^\s*(\w+)\s*(\[\("|\(\["|\[")(.*?)("\)\]|"\]\)|"\])\s*$')
SUBGRAPH_RE = re.compile(r'^\s*subgraph\s+(\w+)\s*\["(.*?)"\]\s*$')
EDGE_OP = r'(<-->|-\.->|-->|---)'

SHAPES = {
    '[("': "shape=cylinder3;boundedLbl=1;backgroundOutline=1;size=12;",
    '(["': "rounded=1;arcSize=50;",
    '["': "rounded=0;",
}
STYLE_NODE = "whiteSpace=wrap;html=1;fillColor=#ECECFF;strokeColor=#9370DB;fontSize=13;"
STYLE_CLUSTER = ("rounded=0;whiteSpace=wrap;html=1;container=1;collapsible=0;"
                 "verticalAlign=top;align=center;fillColor=#FFFFDE;strokeColor=#AAAA33;fontSize=13;")
STYLE_EDGE = "html=1;rounded=1;fontSize=12;labelBackgroundColor=#E8E8E8;"
ARROWS = {
    "-->": "endArrow=block;endFill=1;",
    "-.->": "endArrow=block;endFill=1;dashed=1;",
    "---": "endArrow=none;",
    "<-->": "endArrow=block;endFill=1;startArrow=block;startFill=1;",
}


def parse_mmd(text):
    nodes, clusters, edges, parent = {}, {}, [], {}
    stack = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("flowchart", "graph", "%%")):
            continue
        m = SUBGRAPH_RE.match(line)
        if m:
            clusters[m.group(1)] = m.group(2)
            if stack:
                parent[m.group(1)] = stack[-1]
            stack.append(m.group(1))
            continue
        if line == "end":
            stack.pop()
            continue
        m = NODE_RE.match(line)
        if m:
            nodes[m.group(1)] = (m.group(3), SHAPES[m.group(2)])
            if stack:
                parent[m.group(1)] = stack[-1]
            continue
        # Edge line, possibly a chain: walk operator by operator.
        rest = line
        first = re.match(r"\s*(\w+)", rest)
        if not first:
            raise ValueError(f"cannot parse: {raw}")
        src = first.group(1)
        pos = first.end()
        while pos < len(rest):
            m = re.compile(rf'\s*{EDGE_OP}\s*(?:\|"(.*?)"\|)?\s*(\w+)').match(rest, pos)
            if not m:
                raise ValueError(f"cannot parse: {raw}")
            op, label, dst = m.group(1), m.group(2) or "", m.group(3)
            edges.append((src, op, label, dst))
            src, pos = dst, m.end()
    return nodes, clusters, edges, parent


def render_svg(mmd_path):
    out = Path(tempfile.mkdtemp()) / "d.svg"
    subprocess.run(["mmdc", "-i", str(mmd_path), "-o", str(out), "-q"], check=True, shell=sys.platform == "win32")
    return out.read_text(encoding="utf-8")


def translate(attr):
    m = re.search(r"translate\(\s*([-\d.e]+)[ ,]+([-\d.e]+)\s*\)", attr or "")
    return (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)


def path_extent(element):
    """Width and height spanned by the absolute coordinates of every path under element."""
    xs, ys = [], []
    paths = [element] if element.tag.endswith("path") else list(element.iter("{http://www.w3.org/2000/svg}path"))
    for p in paths:
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", p.get("d", ""))]
        xs += nums[0::2]
        ys += nums[1::2]
    return (max(xs) - min(xs), max(ys) - min(ys)) if xs else (0.0, 0.0)


def layout_from_svg(svg):
    ns = {"s": "http://www.w3.org/2000/svg"}
    root = ET.fromstring(svg)
    boxes, routes = {}, {}
    for g in root.iter("{http://www.w3.org/2000/svg}g"):
        cls = g.get("class", "")
        gid = g.get("id", "")
        if cls.startswith("cluster") and gid:
            r = g.find("s:rect", ns)
            boxes[gid] = (float(r.get("x")), float(r.get("y")), float(r.get("width")), float(r.get("height")))
        elif cls.startswith("node") and gid.startswith("flowchart-"):
            node_id = gid[len("flowchart-"):].rsplit("-", 1)[0]
            cx, cy = translate(g.get("transform"))
            shape = next((c for c in g if "label-container" in (c.get("class") or "")), None)
            if shape is None:
                continue
            if shape.tag.endswith("rect"):
                w, h = float(shape.get("width")), float(shape.get("height"))
            elif shape.get("transform"):
                # A path drawn from its own corner and shifted by -w/2, -h/2
                # (the cylinder): the shift gives the size.
                tx, ty = translate(shape.get("transform"))
                w, h = -2 * tx, -2 * ty
            else:
                # Paths in absolute coordinates around the node centre (the
                # stadium): the extent of their coordinates gives the size.
                w, h = path_extent(shape)
            boxes[node_id] = (cx - w / 2, cy - h / 2, w, h)
    for p in root.iter("{http://www.w3.org/2000/svg}path"):
        if p.get("data-edge") == "true" and p.get("data-points"):
            routes[p.get("data-id")] = json.loads(base64.b64decode(p.get("data-points")))
    return boxes, routes


def build(mmd_text, svg):
    nodes, clusters, edges, parent = parse_mmd(mmd_text)
    boxes, routes = layout_from_svg(svg)

    mxfile = ET.Element("mxfile", host="mmd2drawio")
    diagram = ET.SubElement(mxfile, "diagram", id="d0", name="Page-1")
    model = ET.SubElement(diagram, "mxGraphModel", grid="1", gridSize="10", page="0")
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")

    def origin(cid):
        p = parent.get(cid)
        return (boxes[p][0], boxes[p][1]) if p else (0.0, 0.0)

    def cell(cid, value, style, x, y, w, h):
        ox, oy = origin(cid)
        c = ET.SubElement(root, "mxCell", id=cid, value=value, style=style, vertex="1",
                          parent=parent.get(cid, "1"))
        ET.SubElement(c, "mxGeometry", x=f"{x - ox:.1f}", y=f"{y - oy:.1f}",
                      width=f"{w:.1f}", height=f"{h:.1f}", **{"as": "geometry"})

    # Containers first, outermost before inner, so children find their parent.
    def depth(cid):
        d = 0
        while cid in parent:
            cid, d = parent[cid], d + 1
        return d

    for cid in sorted(clusters, key=depth):
        cell(cid, clusters[cid], STYLE_CLUSTER, *boxes[cid])
    for nid, (label, shape) in nodes.items():
        cell(nid, label, STYLE_NODE + shape, *boxes[nid])

    seen = {}
    for i, (src, op, label, dst) in enumerate(edges):
        k = seen.get((src, dst), 0)
        seen[(src, dst)] = k + 1
        style = STYLE_EDGE + ARROWS[op]
        c = ET.SubElement(root, "mxCell", id=f"e{i}", value=label, style=style, edge="1",
                          parent="1", source=src, target=dst)
        geo = ET.SubElement(c, "mxGeometry", relative="1", **{"as": "geometry"})
        pts = routes.get(f"L_{src}_{dst}_{k}", [])[1:-1]
        if pts:
            arr = ET.SubElement(geo, "Array", **{"as": "points"})
            for p in pts:
                ET.SubElement(arr, "mxPoint", x=f"{p['x']:.1f}", y=f"{p['y']:.1f}")
    return ET.tostring(mxfile, encoding="unicode")


def main():
    mmd, out = Path(sys.argv[1]), Path(sys.argv[2])
    text = mmd.read_text(encoding="utf-8")
    out.write_text(build(text, render_svg(mmd)), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
