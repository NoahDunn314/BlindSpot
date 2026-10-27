"""Deterministic evaluation of the data layer and the explanation validator.

Data-layer checks and explanation checks are reported separately, so a failure points to
the component that caused it. Run from the project root:

    python evaluation/run_eval.py

Tests call run_evaluation() so the same checks run in pytest.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blindspot.discovery import RelationshipDiscovery  # noqa: E402
from blindspot.evidence import FindingValidator  # noqa: E402
from blindspot.investigation import InvestigationAgent, ModelError, investigate, parse_explanation  # noqa: E402
from blindspot.repository import DemoRepository, Repository  # noqa: E402

HARBORLINE = "ent-harborline"
CEDAR = "ent-cedar"
SHARED_FACILITY_FINDING = "shared_upstream:ent-ironvale-p2"
EXPOSURE_FINDING = "event_exposure:ent-ren-0412"


@dataclass(frozen=True)
class Check:
    component: str  # "data layer" or "explanation layer"
    name: str
    passed: bool
    detail: str


class _FailingClient:
    def complete(self, prompt: str) -> str:
        raise ModelError("simulated outage")


class _InventedCitationClient:
    """Returns a plausible explanation that cites a record which does not exist."""

    def complete(self, prompt: str) -> str:
        return json.dumps(
            {
                "title": "Invented",
                "summary": "A supplier has a contract with a regulator.",
                "confirmed_facts": [{"text": "Contract signed.", "evidence_ids": ["SD-999"]}],
                "inferences": [],
                "unknowns": ["Unknown."],
                "why_it_matters": "It matters.",
                "next_steps": ["Check."],
            }
        )


class _PercentClient:
    """Returns a valid citation but states a numeric risk, which Blindspot must never do."""

    def complete(self, prompt: str) -> str:
        return json.dumps(
            {
                "title": "Risk",
                "summary": "There is a 40% chance of disruption.",
                "confirmed_facts": [{"text": "Notice exists.", "evidence_ids": ["RN-0412"]}],
                "inferences": [],
                "unknowns": ["Unknown."],
                "why_it_matters": "It matters.",
                "next_steps": ["Check."],
            }
        )


def _signature(repository: Repository) -> list:
    graph = repository.load_graph()
    discovery = RelationshipDiscovery(graph)
    return [
        (finding.finding_id, finding.score, finding.confidence, finding.evidence_ids)
        for entity in graph.entities
        if entity.kind == "company"
        for finding in discovery.find(entity.entity_id)
    ]


def run_evaluation(repository: Repository | None = None) -> list[Check]:
    repository = repository or DemoRepository()
    graph = repository.load_graph()
    discovery = RelationshipDiscovery(graph)
    checks: list[Check] = []

    def add(component: str, name: str, passed: bool, detail: str = "") -> None:
        checks.append(Check(component, name, bool(passed), detail))

    # Data layer: discovery
    harborline_findings = {finding.finding_id: finding for finding in discovery.find(HARBORLINE)}
    expected = {SHARED_FACILITY_FINDING, EXPOSURE_FINDING}
    found = set(harborline_findings)
    add("data layer", "expected relationships discovered", expected <= found, f"found {sorted(found)}")
    add("data layer", "no unexpected findings for Harborline", found <= expected, f"unexpected: {sorted(found - expected)}")

    shared = harborline_findings.get(SHARED_FACILITY_FINDING)
    suppliers = {path[0].from_entity_id for path in shared.paths} if shared else set()
    add(
        "data layer",
        "shared-upstream finding links the two correct suppliers",
        suppliers == {"ent-pinecrest", "ent-bluefield"},
        f"suppliers: {sorted(suppliers)}",
    )

    add("data layer", "control entity yields no finding", discovery.find(CEDAR) == [], "Cedar Valley Packaging should have none")

    used_records = {rid for finding in harborline_findings.values() for rid in finding.evidence_ids}
    add("data layer", "co-mention NW-0077 is never used as evidence", "NW-0077" not in used_records, "co-mentions are excluded")

    linked = {edge.source_record_id for edge in graph.edges}
    rejected = {rejection.record_id for rejection in graph.rejections}
    add(
        "data layer",
        "ambiguous notice RN-0455 is rejected, not linked",
        "RN-0455" in rejected and "RN-0455" not in linked,
        "Pinecrest is named without state or registry ID and matches two entities",
    )

    edges_with_sources = all(
        edge.source_record_id and edge.source_url for finding in harborline_findings.values() for edge in finding.edges
    )
    add("data layer", "every finding edge keeps its source record and URL", edges_with_sources)

    confidence_ok = bool(shared) and shared.confidence == "Moderate"
    add(
        "data layer",
        "confidence follows the rules (name-and-state supplier edges give Moderate, not High)",
        confidence_ok,
        f"confidence: {shared.confidence if shared else 'n/a'}",
    )

    add(
        "data layer",
        "same input produces identical findings across runs",
        _signature(repository) == _signature(DemoRepository()),
        "two independent runs compared",
    )

    # Explanation layer
    result = investigate(repository, HARBORLINE, InvestigationAgent())
    validation_failures = [note for note in result.notes if "validation" in note]
    add("explanation layer", "deterministic explanations pass validation", not validation_failures, "; ".join(validation_failures))

    total_claims = cited_claims = 0
    for explanation in result.explanations.values():
        for claim in explanation.confirmed_facts + explanation.inferences:
            total_claims += 1
            cited_claims += bool(claim.evidence_ids)
    coverage = cited_claims / total_claims if total_claims else 0.0
    add("explanation layer", "every claim cites evidence", total_claims > 0 and coverage == 1.0, f"{cited_claims}/{total_claims} claims cited")

    uncertainty_ok = all(explanation.unknowns for explanation in result.explanations.values())
    add("explanation layer", "every finding states unknowns and limitations", uncertainty_ok)

    shared_finding = harborline_findings[SHARED_FACILITY_FINDING]
    validator = FindingValidator(shared_finding, result.evidence)
    invented = parse_explanation(_InventedCitationClient().complete(""), mode="live_model")
    invented_errors = validator.validate(invented)
    add(
        "explanation layer",
        "invented citation SD-999 is rejected",
        any("outside this finding" in error for error in invented_errors),
        "; ".join(invented_errors),
    )

    percent = parse_explanation(_PercentClient().complete(""), mode="live_model")
    percent_errors = validator.validate(percent)
    add(
        "explanation layer",
        "numeric risk estimate is rejected",
        any("numeric probability" in error for error in percent_errors),
        "; ".join(percent_errors),
    )

    fallback = investigate(repository, HARBORLINE, InvestigationAgent(_FailingClient()))
    fallback_ok = all(e.mode == "deterministic_demo" for e in fallback.explanations.values()) and any(
        "Live model call failed" in note for note in fallback.notes
    )
    add("explanation layer", "model outage falls back to labeled deterministic explanation", fallback_ok)

    invented_run = investigate(repository, HARBORLINE, InvestigationAgent(_InventedCitationClient()))
    discarded = any("failed validation" in note for note in invented_run.notes)
    all_deterministic = all(e.mode == "deterministic_demo" for e in invented_run.explanations.values())
    add(
        "explanation layer",
        "model output with an invented citation is discarded in favor of the deterministic explanation",
        discarded and all_deterministic,
    )

    refusal = investigate(repository, CEDAR, InvestigationAgent())
    refused = not refusal.findings and not refusal.explanations and any(
        "not evidence that no relationship exists" in note for note in refusal.notes
    )
    add("explanation layer", "insufficient evidence yields a refusal, not a claim", refused, "Cedar Valley Packaging")

    return checks


def main() -> int:
    checks = run_evaluation()
    width = max(len(check.name) for check in checks)
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"[{status}] {check.component:<18} {check.name:<{width}}  {check.detail if not check.passed else ''}".rstrip())
    passed = sum(check.passed for check in checks)
    print(f"\n{passed}/{len(checks)} checks passed.")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
