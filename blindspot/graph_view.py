"""Interactive relationship graph for one finding, rendered with pyvis."""

from __future__ import annotations

from html import escape

from pyvis.network import Network

from blindspot.models import Finding, Graph

# Readability first: white boxes with black text on a dark canvas, a fixed left-to-right
# layout, and the entity type written on each box so meaning never depends on color alone.
BACKGROUND = "#16181c"
NODE_FILL = "#ffffff"
NODE_TEXT = "#000000"
EDGE_COLOR = "#f0f2f5"
EDGE_LABEL = "#ffffff"
MUTED_EDGE = "#8a929c"
MUTED_FILL = "#2c3036"
MUTED_TEXT = "#e6e8eb"
FONT = "Segoe UI, Arial, sans-serif"

# Border accent per role. Paired with the text tag, so color is a second cue, not the only one.
KIND_BORDERS = {
    "target": "#3b82f6",
    "company": "#9ca3af",
    "facility": "#f59e0b",
    "event": "#ef4444",
}
KIND_TAGS = {"target": "TARGET", "company": "SUPPLIER", "facility": "FACILITY", "event": "EVENT"}
# Darker shades for the tag text so it stays at least 4.5:1 against the white fill.
KIND_TAG_TEXT = {
    "target": "#1d4ed8",
    "company": "#374151",
    "facility": "#92400e",
    "event": "#b91c1c",
}
# Columns from left to right: Event -> Facility -> Supplier -> Target.
KIND_LEVELS = {"event": 0, "facility": 1, "company": 2, "target": 3}

RELATION_LABELS = {
    "supplies_to": "supplies to",
    "uses_facility": "uses facility",
    "notice_applies_to": "notice applies to",
    "co_mentioned": "co-mention (not used)",
}


def _role(entity_id: str, kind: str, target_id: str) -> str:
    return "target" if entity_id == target_id else kind


def render_finding_graph(graph: Graph, finding: Finding, height: int = 480) -> str:
    """Return HTML for the finding's entities and documented edges.

    Solid lines are registry-ID matches. Dashed lines are name-and-state matches.
    Grey dotted lines are co-mentions shown for context only. Discovery never uses them.
    """
    entities = graph.entity_map()
    # No font_color here: pyvis would apply it to every node and override the black node text.
    net = Network(height=f"{height}px", width="100%", directed=True, bgcolor=BACKGROUND, cdn_resources="in_line")

    for entity_id in finding.entity_ids:
        entity = entities[entity_id]
        role = _role(entity_id, entity.kind, finding.target_id)
        border = KIND_BORDERS.get(role, KIND_BORDERS["company"])
        net.add_node(
            entity_id,
            label=f"<b>{KIND_TAGS.get(role, role.upper())}</b>\n{entity.name}",
            title=f"{entity.name}\nKind: {entity.kind}\nRegistry ID: {entity.registry_id or 'none'}\nState: {entity.state or 'none'}",
            level=KIND_LEVELS.get(role, 2),
            color={"background": NODE_FILL, "border": border, "highlight": {"background": NODE_FILL, "border": border}},
            shape="box",
            font={"color": NODE_TEXT, "size": 17, "face": FONT, "multi": "html", "bold": {"color": KIND_TAG_TEXT.get(role, NODE_TEXT), "size": 13}},
            margin=14,
            borderWidth=4,
            widthConstraint={"maximum": 210},
            shapeProperties={"borderRadius": 8},
        )

    for edge in finding.edges:
        net.add_edge(
            edge.from_entity_id,
            edge.to_entity_id,
            label=RELATION_LABELS.get(edge.relation, edge.relation),
            title=f"{edge.source_record_id} ({edge.source_id}, {edge.observed_at[:10]})\nMatch: {edge.match_method}",
            color=EDGE_COLOR,
            width=2.5,
            dashes=[10, 6] if edge.match_method != "registry_id" else False,
            arrows={"to": {"enabled": True, "scaleFactor": 1.1}},
            font={"size": 15, "align": "horizontal", "color": EDGE_LABEL, "face": FONT, "strokeWidth": 0, "background": BACKGROUND},
        )

    for edge in graph.edges:
        if edge.status != "co_occurrence" or finding.target_id not in (edge.from_entity_id, edge.to_entity_id):
            continue
        for node_id in (edge.from_entity_id, edge.to_entity_id):
            if node_id not in finding.entity_ids:
                net.add_node(
                    node_id,
                    label=f"<b>CO-MENTION ONLY</b>\n{entities[node_id].name}",
                    level=2,
                    color={"background": MUTED_FILL, "border": MUTED_EDGE},
                    shape="box",
                    font={"color": MUTED_TEXT, "size": 15, "face": FONT, "multi": "html", "bold": {"color": MUTED_TEXT, "size": 12}},
                    margin=12,
                    borderWidth=2,
                    widthConstraint={"maximum": 200},
                    shapeProperties={"borderRadius": 8, "borderDashes": [4, 4]},
                )
        net.add_edge(
            edge.from_entity_id,
            edge.to_entity_id,
            label="co-mention (not used)",
            title=f"{escape(edge.source_record_id)}: article co-mention. Not evidence of a relationship.",
            color=MUTED_EDGE,
            width=1.5,
            dashes=[2, 5],
            # Curve away so a co-mention never sits on top of a documented edge between the same pair.
            smooth={"enabled": True, "type": "curvedCW", "roundness": 0.45},
            font={"size": 13, "align": "horizontal", "color": MUTED_TEXT, "face": FONT, "strokeWidth": 0, "background": BACKGROUND},
        )

    net.set_options(
        """
        {
          "layout": {"hierarchical": {"enabled": true, "direction": "LR", "levelSeparation": 360,
                                      "nodeSpacing": 170, "sortMethod": "directed", "shakeTowards": "roots"}},
          "physics": {"enabled": false},
          "edges": {"smooth": {"type": "cubicBezier", "forceDirection": "horizontal", "roundness": 0.4}},
          "interaction": {"hover": true, "navigationButtons": false, "zoomView": true, "dragView": true}
        }
        """
    )
    # pyvis wraps the canvas in a white bordered card. Match it to the dark theme with rounded corners.
    return net.generate_html().replace(
        "</head>",
        f"<style>body{{margin:0;background:{BACKGROUND};}}"
        f".card{{background:{BACKGROUND};border:1px solid #3a3f46;border-radius:14px;overflow:hidden;}}"
        f"#mynetwork{{border:none!important;background:{BACKGROUND};}}</style></head>",
        1,
    )
