"""Plain data types shared by ingestion, discovery, evidence, and the UI.

Business rules live in the modules that use these types, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceRecord:
    """One raw observation from a source, normalized but with provenance kept."""

    record_id: str
    source_id: str
    record_type: str  # "entity_reference" or "relationship"
    observed_at: str  # ISO-8601 timestamp as reported by the source
    url: str  # where the record came from (fixture:// URI for demo data)
    payload: dict
    is_synthetic: bool = True


@dataclass(frozen=True)
class Entity:
    entity_id: str
    kind: str  # "company", "facility", or "event"
    name: str
    registry_id: str | None = None
    state: str | None = None


@dataclass(frozen=True)
class Edge:
    """A documented relationship between two resolved entities."""

    edge_id: str
    from_entity_id: str
    to_entity_id: str
    relation: str
    status: str  # "documented" or "co_occurrence"
    source_id: str
    source_record_id: str
    source_url: str
    observed_at: str
    match_method: str  # "registry_id", "name_and_state", or "name_only"
    match_rationale: str


@dataclass(frozen=True)
class Rejection:
    """A mention that could not be linked to exactly one entity."""

    record_id: str
    mention_name: str
    reason: str


@dataclass(frozen=True)
class Graph:
    entities: tuple[Entity, ...]
    edges: tuple[Edge, ...]
    rejections: tuple[Rejection, ...]

    def entity_map(self) -> dict[str, Entity]:
        return {entity.entity_id: entity for entity in self.entities}


@dataclass(frozen=True)
class Evidence:
    record_id: str
    source_id: str
    source_name: str
    url: str
    observed_at: str
    is_synthetic: bool
    summary: str


@dataclass(frozen=True)
class Finding:
    """A candidate connection found by deterministic discovery rules."""

    finding_id: str
    pattern: str  # "shared_upstream" or "event_exposure"
    title: str
    target_id: str
    paths: tuple[tuple[Edge, ...], ...]
    score: int
    score_breakdown: dict
    confidence: str  # "High", "Moderate", or "Low" (rule-based, not a probability)

    @property
    def edges(self) -> tuple[Edge, ...]:
        unique = {edge.edge_id: edge for path in self.paths for edge in path}
        return tuple(unique[key] for key in sorted(unique))

    @property
    def entity_ids(self) -> tuple[str, ...]:
        ids = {self.target_id}
        for edge in self.edges:
            ids.add(edge.from_entity_id)
            ids.add(edge.to_entity_id)
        return tuple(sorted(ids))

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(sorted({edge.source_record_id for edge in self.edges}))


@dataclass(frozen=True)
class Claim:
    text: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class Explanation:
    """Human-readable interpretation of one finding. Never the source of facts."""

    title: str
    summary: str
    confirmed_facts: tuple[Claim, ...]
    inferences: tuple[Claim, ...]
    unknowns: tuple[str, ...]
    why_it_matters: str
    next_steps: tuple[str, ...]
    mode: str  # "deterministic_demo" or "live_model"


@dataclass
class InvestigationResult:
    target: Entity
    repository_mode: str  # "demo" or "snowflake"
    findings: list[Finding] = field(default_factory=list)
    explanations: dict[str, Explanation] = field(default_factory=dict)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    rejections: list[Rejection] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
