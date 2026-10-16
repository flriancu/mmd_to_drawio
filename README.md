# mmd2drawio

Converts a Mermaid flowchart into an editable draw.io diagram, so a diagram can
live as Mermaid in a repository and still be published as native draw.io
(for example in Confluence, which has the draw.io app but no Mermaid).

Mermaid stays the source of truth: generate the `.drawio` when you publish, never
edit it and carry the edits back by hand.

## How it works

1. `mmdc` (the Mermaid CLI) renders the `.mmd` to SVG. That fixes the layout:
   - node centres and sizes
   - cluster boxes
   - edge routes, which Mermaid stores base64-encoded in `data-points`
2. The `.mmd` source supplies the meaning: labels, node shapes, and dashed,
   undirected or bidirectional edges.
3. The output is native draw.io cells, not an embedded image:
   - subgraphs become containers that hold their nodes
   - edges keep Mermaid's routing as waypoints

## Usage

```
python mmd2drawio.py <diagram.mmd> <diagram.drawio>
```

Check the result without opening the editor, using draw.io desktop's exporter:

```
"C:\Program Files\draw.io\draw.io.exe" -x -f png -s 2 -o out.png diagram.drawio
```

`examples/` holds two diagrams from the DMS acquisition north-star document.

## Requirements

- Python 3.9+ (standard library only)
- Mermaid CLI on `PATH`: `npm install -g @mermaid-js/mermaid-cli`

## Supported Mermaid subset

It covers flowcharts, which is what the documents using it need:

- `flowchart` / `graph` header, any direction
- `subgraph id["label"] ... end`, nested
- nodes `A["text"]` (box), `A[("text")]` (cylinder), `A(["text"])` (stadium);
  labels may contain `<br/>`
- edges `-->`, `-.->` (dashed), `---` (no arrow), `<-->` (both ends), each
  optionally labelled `|"text"|`, and chains `A --> B --> C`

Anything else fails with `cannot parse: <line>` rather than being dropped
silently.

## Limitations

- Edge labels sit at draw.io's default position (the middle of the edge), not
  exactly where Mermaid placed them.
- Styles are fixed to Mermaid's default theme colours; `classDef` and `style`
  lines are not supported.
- Node identity in the SVG is read from Mermaid's `flowchart-<id>-<n>` element
  ids, and edges from `L_<src>_<dst>_<n>`. A Mermaid release that changes those
  naming schemes will need a matching change here.
