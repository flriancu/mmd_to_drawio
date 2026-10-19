"""Convert a Mermaid flowchart into an editable draw.io diagram.

    python mmd2drawio.py <diagram.mmd> <diagram.drawio> [--font-size N]

Mermaid stays the source of truth. This renders it with the Mermaid CLI (`mmdc`)
to SVG, takes the layout from there (node centres and sizes, cluster boxes, edge
routes from `data-points`) and the meaning from the .mmd source (labels,
shapes, dashed, bidirectional and undirected edges, links), and writes native
draw.io cells: clusters become containers holding their nodes, so the result can
be edited in draw.io without falling apart.

Handles the flowchart subset these docs use: `subgraph id["label"] ... end`,
nodes `A["x"]`, `A[("x")]` (cylinder), `A(["x"])` (stadium), and edges
`-->`, `-.->`, `---`, `<-->`, each optionally labelled `|"x"|`, including
chains `A --> B --> C`. Links: Mermaid's `click A href "url"` for a node, and a
`%% link "edge label" "url"` comment for every edge with that label, which
Mermaid ignores.

--font-size lays the diagram out for that font size, in pixels, and writes every
label at it, so the boxes grow with the text instead of the text spilling out.
"""
import argparse
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
CLICK_RE = re.compile(r'^\s*click\s+(\w+)\s+(?:href\s+)?"([^"]+)"')
EDGE_LINK_RE = re.compile(r'^%%\s*link\s+"(.*?)"\s+"([^"]+)"\s*$')
EDGE_OP = r'(<-->|-\.->|-->|---)'

SHAPES = {
    '[("': "shape=cylinder3;boundedLbl=1;backgroundOutline=1;size=12;",
    '(["': "rounded=1;arcSize=50;",
    '["': "rounded=0;",
}
STYLE_NODE = "whiteSpace=wrap;html=1;fillColor=#ECECFF;strokeColor=#9370DB;fontSize={node};"
STYLE_CLUSTER = ("rounded=0;whiteSpace=wrap;html=1;container=1;collapsible=0;"
                 "verticalAlign=top;align=center;fillColor=#FFFFDE;strokeColor=#AAAA33;fontSize={node};")
STYLE_EDGE = "html=1;rounded=1;fontSize={edge};labelBackgroundColor=#E8E8E8;"
STYLE_LINK = "fontColor=#0B57D0;fontStyle=4;"
ARROWS = {
    "-->": "endArrow=block;endFill=1;",
    "-.->": "endArrow=block;endFill=1;dashed=1;",
    "---": "endArrow=none;",
    "<-->": "endArrow=block;endFill=1;startArrow=block;startFill=1;",
}
# Without --font-size, the sizes draw.io has always been given here.
DEFAULT_SIZES = {"node": 13, "edge": 12}


def parse_mmd(text):
    nodes, clusters, edges, parent = {}, {}, [], {}
    node_links, edge_links = {}, {}
    stack = []
    for raw in text.splitlines():
        line = raw.strip()
        m = EDGE_LINK_RE.match(line)
        if m:
            edge_links[m.group(1)] = m.group(2)
            continue
        if not line or line.startswith(("flowchart", "graph", "%%")):
            continue
        m = CLICK_RE.match(line)
        if m:
            node_links[m.group(1)] = m.group(2)
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

    # A link that names nothing is a typo, not a diagram without that link.
    for nid in node_links:
        if nid not in nodes and nid not in clusters:
            raise ValueError(f"click on unknown node: {nid}")
    labels = {label for _, _, label, _ in edges}
    for label in edge_links:
        if label not in labels:
            raise ValueError(f'link for an edge label no edge has: "{label}"')
    return nodes, clusters, edges, parent, node_links, edge_links


def render_svg(mmd_path, font_size=None):
    work = Path(tempfile.mkdtemp())
    out = work / "d.svg"
    cmd = ["mmdc", "-i", str(mmd_path), "-o", str(out), "-q"]
    if font_size:
        # Mermaid measures labels at this size, so the layout leaves room for them.
        config = work / "config.json"
        config.write_text(json.dumps({"themeVariables": {"fontSize": f"{font_size}px"}}), encoding="utf-8")
        cmd += ["-c", str(config)]
    subprocess.run(cmd, check=True, shell=sys.platform == "win32")
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
    # A node with a link sits inside an <a>, and the <a> carries its position.
    link_of = {child: a for a in root.iter("{http://www.w3.org/2000/svg}a") for child in a}
    for g in root.iter("{http://www.w3.org/2000/svg}g"):
        cls = g.get("class", "")
        gid = g.get("id", "")
        if cls.startswith("cluster") and gid:
            r = g.find("s:rect", ns)
            boxes[gid] = (float(r.get("x")), float(r.get("y")), float(r.get("width")), float(r.get("height")))
        elif cls.startswith("node") and gid.startswith("flowchart-"):
            node_id = gid[len("flowchart-"):].rsplit("-", 1)[0]
            cx, cy = translate(g.get("transform") or link_of.get(g, g).get("transform"))
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
    # Where Mermaid put each edge label: its layout keeps labels from overlapping.
    labels = {}
    for g in root.iter("{http://www.w3.org/2000/svg}g"):
        if g.get("class", "").startswith("edgeLabel"):
            inner = g.find("s:g[@data-id]", ns)
            if inner is not None:
                labels[inner.get("data-id")] = translate(g.get("transform"))
    return boxes, routes, labels


def midpoint(points):
    """The point halfway along a polyline, which is where draw.io puts an edge label."""
    segments = list(zip(points, points[1:]))
    lengths = [((b["x"] - a["x"]) ** 2 + (b["y"] - a["y"]) ** 2) ** 0.5 for a, b in segments]
    remaining = sum(lengths) / 2
    for (a, b), length in zip(segments, lengths):
        if remaining <= length and length > 0:
            t = remaining / length
            return a["x"] + t * (b["x"] - a["x"]), a["y"] + t * (b["y"] - a["y"])
        remaining -= length
    return points[-1]["x"], points[-1]["y"]


def build(mmd_text, svg, font_size=None):
    nodes, clusters, edges, parent, node_links, edge_links = parse_mmd(mmd_text)
    boxes, routes, labels = layout_from_svg(svg)
    sizes = {"node": font_size, "edge": font_size} if font_size else DEFAULT_SIZES
    style_node, style_cluster, style_edge = (s.format(**sizes) for s in (STYLE_NODE, STYLE_CLUSTER, STYLE_EDGE))

    mxfile = ET.Element("mxfile", host="mmd2drawio")
    diagram = ET.SubElement(mxfile, "diagram", id="d0", name="Page-1")
    model = ET.SubElement(diagram, "mxGraphModel", grid="1", gridSize="10", page="0")
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")

    def element(cid, value, style, link, **attrs):
        """An mxCell, or one wrapped in a UserObject carrying the link: how draw.io
        stores a cell that opens a URL when clicked."""
        if not link:
            return ET.SubElement(root, "mxCell", id=cid, value=value, style=style, **attrs)
        wrapper = ET.SubElement(root, "UserObject", id=cid, label=value, link=link)
        return ET.SubElement(wrapper, "mxCell", style=style + STYLE_LINK, **attrs)

    def origin(cid):
        p = parent.get(cid)
        return (boxes[p][0], boxes[p][1]) if p else (0.0, 0.0)

    def cell(cid, value, style, x, y, w, h):
        ox, oy = origin(cid)
        c = element(cid, value, style, node_links.get(cid), vertex="1", parent=parent.get(cid, "1"))
        ET.SubElement(c, "mxGeometry", x=f"{x - ox:.1f}", y=f"{y - oy:.1f}",
                      width=f"{w:.1f}", height=f"{h:.1f}", **{"as": "geometry"})

    # Containers first, outermost before inner, so children find their parent.
    def depth(cid):
        d = 0
        while cid in parent:
            cid, d = parent[cid], d + 1
        return d

    for cid in sorted(clusters, key=depth):
        cell(cid, clusters[cid], style_cluster, *boxes[cid])
    for nid, (label, shape) in nodes.items():
        cell(nid, label, style_node + shape, *boxes[nid])

    seen = {}
    for i, (src, op, label, dst) in enumerate(edges):
        k = seen.get((src, dst), 0)
        seen[(src, dst)] = k + 1
        c = element(f"e{i}", label, style_edge + ARROWS[op], edge_links.get(label) if label else None,
                    edge="1", parent="1", source=src, target=dst)
        geo = ET.SubElement(c, "mxGeometry", relative="1", **{"as": "geometry"})
        route = routes.get(f"L_{src}_{dst}_{k}", [])
        place = labels.get(f"L_{src}_{dst}_{k}")
        if label and place and len(route) >= 2:
            # draw.io puts the label halfway along the edge; move it to Mermaid's spot.
            mx, my = midpoint(route)
            ET.SubElement(geo, "mxPoint", x=f"{place[0] - mx:.1f}", y=f"{place[1] - my:.1f}", **{"as": "offset"})
        pts = route[1:-1]
        if pts:
            arr = ET.SubElement(geo, "Array", **{"as": "points"})
            for p in pts:
                ET.SubElement(arr, "mxPoint", x=f"{p['x']:.1f}", y=f"{p['y']:.1f}")
    return ET.tostring(mxfile, encoding="unicode")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("mmd", type=Path, help="Mermaid flowchart to convert")
    parser.add_argument("drawio", type=Path, help="draw.io file to write")
    parser.add_argument("--font-size", type=int, default=None,
                        help="font size in pixels for every label, laid out to fit")
    args = parser.parse_args()
    text = args.mmd.read_text(encoding="utf-8")
    args.drawio.write_text(build(text, render_svg(args.mmd, args.font_size), args.font_size), encoding="utf-8")
    print(f"wrote {args.drawio}")


if __name__ == "__main__":
    main()
