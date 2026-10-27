from blindspot.adapters import demo_adapters, ingest
from blindspot.discovery import RelationshipDiscovery, build_graph, score_edges
from blindspot.models import Edge


def _graph():
    return build_graph(ingest(demo_adapters()))


def test_harborline_shared_facility_is_discovered_with_both_suppliers():
    findings = {f.finding_id: f for f in RelationshipDiscovery(_graph()).find("ent-harborline")}
    shared = findings["shared_upstream:ent-ironvale-p2"]
    assert {path[0].from_entity_id for path in shared.paths} == {"ent-pinecrest", "ent-bluefield"}
    assert {path[1].to_entity_id for path in shared.paths} == {"ent-ironvale-p2"}


def test_notice_reaches_target_through_shared_facility_in_three_edges():
    findings = {f.finding_id: f for f in RelationshipDiscovery(_graph()).find("ent-harborline")}
    exposure = findings["event_exposure:ent-ren-0412"]
    assert all(len(path) == 3 for path in exposure.paths)
    assert "RN-0412" in exposure.evidence_ids


def test_co_mentions_never_create_findings_or_evidence():
    graph = _graph()
    assert any(edge.status == "co_occurrence" for edge in graph.edges)
    for finding in RelationshipDiscovery(graph).find("ent-harborline"):
        assert "NW-0077" not in finding.evidence_ids


def test_control_entity_with_no_shared_link_yields_no_finding():
    assert RelationshipDiscovery(_graph()).find("ent-cedar") == []


def test_discovery_is_reproducible():
    first = [(f.finding_id, f.score, f.evidence_ids) for f in RelationshipDiscovery(_graph()).find("ent-harborline")]
    second = [(f.finding_id, f.score, f.evidence_ids) for f in RelationshipDiscovery(_graph()).find("ent-harborline")]
    assert first == second


def test_unresolved_mention_is_reported_as_rejection_not_edge():
    graph = _graph()
    assert any(r.record_id == "RN-0455" for r in graph.rejections)
    assert all(edge.source_record_id != "RN-0455" for edge in graph.edges)


def test_score_rules_and_confidence_labels():
    def edge(method, source):
        return Edge("e", "a", "b", "supplies_to", "documented", source, "r", "u", "t", method, "")

    high = score_edges((edge("registry_id", "s1"), edge("registry_id", "s2")))
    moderate = score_edges((edge("registry_id", "s1"), edge("name_and_state", "s2")))
    low = score_edges((edge("name_only", "s1"), edge("registry_id", "s2")))
    single = score_edges((edge("registry_id", "s1"),))

    assert high[2] == "High"
    assert moderate[2] == "Moderate"
    assert low[2] == "Low"
    assert single[2] == "Low"
    assert high[0] == 4 + 2
    assert low[1]["name_only_penalty"] == -2
