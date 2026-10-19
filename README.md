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
2. The `.mmd` source supplies the meaning: labels, node shapes, links, and
   dashed, undirected or bidirectional edges.
3. The output is native draw.io cells, not an embedded image:
   - subgraphs become containers that hold their nodes
   - edges keep Mermaid's routing as waypoints, and their labels Mermaid's
     placement
   - a linked node or edge opens its URL when clicked, and its label is drawn as
     a link

## Usage

```
python mmd2drawio.py <diagram.mmd> <diagram.drawio> [--font-size N]
```

`--font-size` sets every label to N pixels and has Mermaid lay the diagram out at
that size, so boxes grow to fit the text. Without it, labels are 13 px (edges
12 px) on Mermaid's default layout.

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
- links: `click A href "url"` on a node, as in Mermaid, and
  `%% link "edge label" "url"` for every edge with that label. Mermaid has no
  links on edges; to Mermaid the line is a comment, so the source still renders
  there.

A `click` on an unknown node, or a `%% link` naming a label no edge has, fails
rather than producing a diagram without that link.

Anything else fails with `cannot parse: <line>` rather than being dropped
silently.

## Limitations

- Styles are fixed to Mermaid's default theme colours; `classDef` and `style`
  lines are not supported.
- Node identity in the SVG is read from Mermaid's `flowchart-<id>-<n>` element
  ids, and edges and their labels from `L_<src>_<dst>_<n>`. A Mermaid release
  that changes those naming schemes will need a matching change here.
