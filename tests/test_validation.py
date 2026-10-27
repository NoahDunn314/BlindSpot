import json

import pytest

from blindspot.adapters import demo_adapters, ingest
from blindspot.discovery import RelationshipDiscovery, build_graph
from blindspot.evidence import EvidenceRetriever, FindingValidator
from blindspot.investigation import (
    InvestigationAgent,
    ModelError,
    deterministic_explanation,
    investigate,
    parse_explanation,
)
from blindspot.models import Claim, Explanation
from blindspot.repository import DemoRepository


@pytest.fixture(scope="module")
def shared_finding_and_evidence():
    adapters = demo_adapters()
    records = ingest(adapters)
    graph = build_graph(records)
    finding = {f.finding_id: f for f in RelationshipDiscovery(graph).find("ent-harborline")}["shared_upstream:ent-ironvale-p2"]
    source_names = {a.info.source_id: a.info.name for a in adapters}
    evidence = EvidenceRetriever(records, source_names).get(finding.evidence_ids)
    return finding, evidence


def _explanation(**overrides):
    base = dict(
        title="t",
        summary="s",
        confirmed_facts=(Claim("documented", ("SD-001",)),),
        inferences=(Claim("inferred", ("FR-101",)),),
        unknowns=("unknown",),
        why_it_matters="matters",
        next_steps=("check",),
        mode="live_model",
    )
    base.update(overrides)
    return Explanation(**base)


def test_deterministic_explanation_passes_validation(shared_finding_and_evidence):
    finding, evidence = shared_finding_and_evidence
    names = {e.entity_id: e.name for e in DemoRepository().load_graph().entities}
    explanation = deterministic_explanation(finding, evidence, names)
    assert FindingValidator(finding, evidence).validate(explanation) == []


def test_citation_outside_finding_is_rejected(shared_finding_and_evidence):
    finding, evidence = shared_finding_and_evidence
    errors = FindingValidator(finding, evidence).validate(_explanation(confirmed_facts=(Claim("x", ("SD-999",)),)))
    assert any("outside this finding" in error for error in errors)


def test_fact_without_evidence_is_rejected(shared_finding_and_evidence):
    finding, evidence = shared_finding_and_evidence
    errors = FindingValidator(finding, evidence).validate(_explanation(inferences=(Claim("x", ()),)))
    assert any("has no evidence" in error for error in errors)


def test_numeric_probability_is_rejected(shared_finding_and_evidence):
    finding, evidence = shared_finding_and_evidence
    errors = FindingValidator(finding, evidence).validate(_explanation(summary="A 35% chance of disruption."))
    assert any("numeric probability" in error for error in errors)


def test_missing_unknowns_are_rejected(shared_finding_and_evidence):
    finding, evidence = shared_finding_and_evidence
    errors = FindingValidator(finding, evidence).validate(_explanation(unknowns=()))
    assert any("unknowns" in error for error in errors)


def test_parse_rejects_malformed_model_output():
    with pytest.raises(ValueError):
        parse_explanation("no json here", mode="live_model")
    with pytest.raises(ValueError):
        parse_explanation(json.dumps({"title": "only a title"}), mode="live_model")


class _ValidModel:
    def complete(self, prompt):
        return json.dumps(
            {
                "title": "Shared facility",
                "summary": "Two suppliers both use one facility.",
                "confirmed_facts": [{"text": "SD-001 lists Pinecrest.", "evidence_ids": ["SD-001"]}],
                "inferences": [{"text": "They may share a dependency.", "evidence_ids": ["FR-101", "FR-102"]}],
                "unknowns": ["Volumes are unknown."],
                "why_it_matters": "Continuity risk.",
                "next_steps": ["Ask the suppliers."],
            }
        )


def test_valid_model_output_is_used_and_labeled_live():
    outcome = investigate(DemoRepository(), "ent-harborline", InvestigationAgent(_ValidModel()))
    assert {e.mode for e in outcome.explanations.values()} == {"live_model"}
    assert not any("failed validation" in note for note in outcome.notes)


def test_model_outage_falls_back_with_visible_note():
    class Down:
        def complete(self, prompt):
            raise ModelError("down")

    outcome = investigate(DemoRepository(), "ent-harborline", InvestigationAgent(Down()))
    assert outcome.explanations
    assert all(e.mode == "deterministic_demo" for e in outcome.explanations.values())
    assert any("Live model call failed" in note for note in outcome.notes)
