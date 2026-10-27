"""Interactive relationship graph for one finding, rendered with pyvis."""

from __future__ import annotations

from html import escape

from pyvis.network import Network

from blindspot.models import Finding, Graph

KIND_COLORS = {
    "target": "#1d4ed8",
    "company": "#475569",
    "facility": "#b45309",
    "event": "#b91c1c",
}
RELATION_LABELS = {
    "supplies_to": "supplies to",
    "uses_facility": "uses facility",
    "notice_applies_to": "notice applies to",
    "co_mentioned": "co-mention (not used)",
}


def render_finding_graph(graph: Graph, finding: Finding, height: int = 480) -> str:
    """Return HTML for the finding's entities and documented edges.

    Solid lines are registry-ID matches. Dashed lines are name-and-state matches.
    Grey dotted lines are co-mentions shown for context only. Discovery never uses them.
    """
    entities = graph.entity_map()
    net = Network(height=f"{height}px", width="100%", directed=True, bgcolor="#ffffff", font_color="#1f2933", cdn_resources="in_line")

    for entity_id in finding.entity_ids:
        entity = entities[entity_id]
        color = KIND_COLORS["target"] if entity_id == finding.target_id else KIND_COLORS.get(entity.kind, "#475569")
        net.add_node(
            entity_id,
            label=entity.name,
            title=f"{entity.name}\nKind: {entity.kind}\nRegistry ID: {entity.registry_id or 'none'}\nState: {entity.state or 'none'}",
            color=color,
            shape="box" if entity.kind == "facility" else ("diamond" if entity.kind == "event" else "ellipse"),
            font={"color": "#ffffff", "size": 14, "face": "Segoe UI, Arial, sans-serif"},
            margin=10,
        )

    for edge in finding.edges:
        net.add_edge(
            edge.from_entity_id,
            edge.to_entity_id,
            label=RELATION_LABELS.get(edge.relation, edge.relation),
            title=f"{edge.source_record_id} ({edge.source_id}, {edge.observed_at[:10]})\nMatch: {edge.match_method}",
            color="#334155",
            dashes=edge.match_method != "registry_id",
            arrows="to",
            font={"size": 11, "align": "middle", "color": "#334155"},
        )

    for edge in graph.edges:
        if edge.status != "co_occurrence" or finding.target_id not in (edge.from_entity_id, edge.to_entity_id):
            continue
        for node_id in (edge.from_entity_id, edge.to_entity_id):
            if node_id not in finding.entity_ids:
                net.add_node(node_id, label=entities[node_id].name, color="#cbd5e1", shape="ellipse", font={"color": "#334155", "size": 12})
        net.add_edge(
            edge.from_entity_id,
            edge.to_entity_id,
            label="co-mention (not used)",
            title=f"{escape(edge.source_record_id)}: article co-mention. Not evidence of a relationship.",
            color="#94a3b8",
            dashes=[2, 4],
            font={"size": 10, "color": "#64748b"},
        )

    net.set_options(
        """
        {
          "physics": {"barnesHut": {"gravitationalConstant": -4000, "springLength": 170}},
          "interaction": {"hover": true, "navigationButtons": false}
        }
        """
    )
    return net.generate_html()
