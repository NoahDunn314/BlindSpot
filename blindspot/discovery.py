"""Deterministic relationship discovery.

Two explicit patterns are supported. Both use only documented edges, and both
are short (two or three edges per branch):

  shared_upstream:   Target <-supplies_to- S1 -uses_facility-> F <-uses_facility- S2 -supplies_to-> Target
  event_exposure:    Target <-supplies_to- S -uses_facility-> F <-notice_applies_to- Event

Co-mentions (status "co_occurrence") are never used. Two names appearing in one
article is not a relationship. Scores are transparent point totals used only for
ranking. They are not probabilities of real-world risk.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from blindspot.models import Edge, Entity, Finding, Graph, Rejection, SourceRecord
from blindspot.resolution import METHOD_RANK, EntityResolver

SUPPLIES_TO = "supplies_to"
USES_FACILITY = "uses_facility"
NOTICE_APPLIES_TO = "notice_applies_to"
DOCUMENTED = "documented"


def build_graph(records: list[SourceRecord]) -> Graph:
    """Turn normalized records into resolved entities, edges, and rejections."""
    entities: dict[str, Entity] = {}
    for record in records:
        if record.record_type != "entity_reference":
            continue
        payload = record.payload
        entities.setdefault(
            payload["entity_id"],
            Entity(
                entity_id=payload["entity_id"],
                kind=payload["kind"],
                name=payload["name"],
                registry_id=payload.get("registry_id"),
                state=payload.get("state"),
            ),
        )

    resolver = EntityResolver(entities.values())
    edges: list[Edge] = []
    rejections: list[Rejection] = []

    for record in records:
        if record.record_type != "relationship":
            continue
        payload = record.payload
        subject_mention, object_mention = payload["subject"], payload["object"]
        subject = resolver.resolve(subject_mention)
        obj = resolver.resolve(object_mention)

        failed = False
        for mention, resolution in ((subject_mention, subject), (object_mention, obj)):
            if resolution.entity_id is None:
                failed = True
                rejections.append(Rejection(record.record_id, mention.get("name", ""), resolution.reason))
        if failed:
            continue

        method = min(subject.method, obj.method, key=METHOD_RANK.get)
        edges.append(
            Edge(
                edge_id=record.record_id,
                from_entity_id=subject.entity_id,
                to_entity_id=obj.entity_id,
                relation=payload["relation"],
                status=payload.get("status", DOCUMENTED),
                source_id=record.source_id,
                source_record_id=record.record_id,
                source_url=record.url,
                observed_at=record.observed_at,
                match_method=method,
                match_rationale=f"subject: {subject.reason}; object: {obj.reason}",
            )
        )

    return Graph(
        entities=tuple(sorted(entities.values(), key=lambda e: e.entity_id)),
        edges=tuple(sorted(edges, key=lambda e: e.edge_id)),
        rejections=tuple(sorted(rejections, key=lambda r: (r.record_id, r.mention_name))),
    )


def score_edges(edges: tuple[Edge, ...]) -> tuple[int, dict, str]:
    """Return (score, breakdown, confidence label) for a set of edges.

    Rules:
      +2 per distinct source that documents part of the finding
      +1 per edge matched by registry ID
      -2 per edge matched only by name
    Confidence:
      Low       if any edge is name-only, or fewer than two sources support it
      High      if every edge is registry-matched and at least two sources support it
      Moderate  otherwise
    """
    distinct_sources = len({edge.source_id for edge in edges})
    registry_matched = sum(1 for edge in edges if edge.match_method == "registry_id")
    name_only = sum(1 for edge in edges if edge.match_method == "name_only")

    breakdown = {
        "source_points": 2 * distinct_sources,
        "registry_match_points": registry_matched,
        "name_only_penalty": -2 * name_only,
    }
    score = sum(breakdown.values())
    breakdown["total"] = score

    if name_only or distinct_sources < 2:
        confidence = "Low"
    elif registry_matched == len(edges):
        confidence = "High"
    else:
        confidence = "Moderate"
    return score, breakdown, confidence


def join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


class RelationshipDiscovery:
    """Finds short, documented connection patterns around one target entity."""

    def __init__(self, graph: Graph):
        self.graph = graph
        self.entities = graph.entity_map()
        self.documented = [edge for edge in graph.edges if edge.status == DOCUMENTED]

    def _kind(self, entity_id: str) -> str:
        return self.entities[entity_id].kind

    def _name(self, entity_id: str) -> str:
        return self.entities[entity_id].name

    def _suppliers_of(self, target_id: str) -> dict[str, Edge]:
        return {
            edge.from_entity_id: edge
            for edge in self.documented
            if edge.relation == SUPPLIES_TO and edge.to_entity_id == target_id
        }

    def find(self, target_id: str) -> list[Finding]:
        if target_id not in self.entities:
            raise KeyError(f"Unknown target entity: {target_id}")
        findings = self._shared_upstream(target_id) + self._event_exposure(target_id)
        return sorted(findings, key=lambda f: (-f.score, f.finding_id))

    def _facility_uses(self, supplier_ids: set[str]) -> dict[str, list[Edge]]:
        """Map facility ID to the uses_facility edges that connect it to any supplier."""
        uses_by_facility: dict[str, list[Edge]] = defaultdict(list)
        for edge in self.documented:
            if edge.relation == USES_FACILITY and edge.from_entity_id in supplier_ids and self._kind(edge.to_entity_id) == "facility":
                uses_by_facility[edge.to_entity_id].append(edge)
        return uses_by_facility

    def _shared_upstream(self, target_id: str) -> list[Finding]:
        suppliers = self._suppliers_of(target_id)
        uses_by_facility = self._facility_uses(set(suppliers))

        findings = []
        for facility_id, uses in sorted(uses_by_facility.items()):
            if len({edge.from_entity_id for edge in uses}) < 2:
                continue
            paths = tuple(
                (suppliers[use.from_entity_id], use)
                for use in sorted(uses, key=lambda e: e.from_entity_id)
            )
            supplier_names = [self._name(use.from_entity_id) for use in sorted(uses, key=lambda e: e.from_entity_id)]
            findings.append(
                self._make_finding(
                    pattern="shared_upstream",
                    finding_id=f"shared_upstream:{facility_id}",
                    target_id=target_id,
                    paths=paths,
                    title=f"{join_names(supplier_names)} both use {self._name(facility_id)}",
                )
            )
        return findings

    def _event_exposure(self, target_id: str) -> list[Finding]:
        suppliers = self._suppliers_of(target_id)
        uses_by_facility = self._facility_uses(set(suppliers))

        findings = []
        for edge in self.documented:
            if edge.relation != NOTICE_APPLIES_TO or self._kind(edge.from_entity_id) != "event":
                continue
            uses = uses_by_facility.get(edge.to_entity_id)
            if not uses:
                continue
            paths = tuple(
                (suppliers[use.from_entity_id], use, edge)
                for use in sorted(uses, key=lambda e: e.from_entity_id)
            )
            findings.append(
                self._make_finding(
                    pattern="event_exposure",
                    finding_id=f"event_exposure:{edge.from_entity_id}",
                    target_id=target_id,
                    paths=paths,
                    title=f"{self._name(edge.from_entity_id)} reaches {self._name(target_id)} through {self._name(edge.to_entity_id)}",
                )
            )
        return findings

    def _make_finding(self, pattern: str, finding_id: str, target_id: str, paths: tuple, title: str) -> Finding:
        all_edges = tuple({edge.edge_id: edge for path in paths for edge in path}.values())
        score, breakdown, confidence = score_edges(all_edges)
        return Finding(
            finding_id=finding_id,
            pattern=pattern,
            title=title,
            target_id=target_id,
            paths=paths,
            score=score,
            score_breakdown=breakdown,
            confidence=confidence,
        )
