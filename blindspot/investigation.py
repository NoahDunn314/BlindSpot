"""Investigation workflow and the explanation layer.

Discovery (blindspot.discovery) decides what is connected. This module only
explains a finding that discovery already produced. The model never decides which
facts exist. Its output is validated against the finding's evidence before it is shown.

Without an API key, the deterministic explanation is used and labeled as a demo.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping, Protocol

from blindspot.discovery import RelationshipDiscovery, join_names
from blindspot.evidence import EvidenceRetriever, FindingValidator
from blindspot.models import Claim, Entity, Evidence, Explanation, Finding, InvestigationResult
from blindspot.repository import Repository
from blindspot.resolution import normalize_name

METHOD_LABELS = {"registry_id": "registry ID", "name_and_state": "name and state", "name_only": "name only"}
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


class ModelError(RuntimeError):
    """Raised when the model provider cannot be reached or returns an error."""


class ModelClient(Protocol):
    def complete(self, prompt: str) -> str: ...


@dataclass
class AnthropicClient:
    api_key: str
    model: str
    timeout: float = 60.0

    def complete(self, prompt: str) -> str:
        body = json.dumps(
            {"model": self.model, "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]}
        ).encode("utf-8")
        request = urllib.request.Request(
            ANTHROPIC_URL,
            data=body,
            method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ModelError(f"model API returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ModelError("model API could not be reached") from exc
        return "".join(block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text")


def build_prompt(finding: Finding, evidence: Mapping[str, Evidence], names: Mapping[str, str]) -> str:
    lines = [
        "You are explaining one connection that a deterministic data pipeline found.",
        "Rules:",
        "- Use only the evidence below. Do not add facts, companies, dates, or citations.",
        "- Cite evidence only by its record_id. Every confirmed fact and inference needs at least one record_id.",
        "- Separate confirmed facts (documented in a record) from inferences (your reasoning).",
        "- Do not give percentages or probabilities.",
        "- If the evidence is weak, say so in unknowns.",
        "- Return one JSON object only, with keys: title, summary, confirmed_facts, inferences, unknowns, why_it_matters, next_steps.",
        "  confirmed_facts and inferences are lists of {\"text\": str, \"evidence_ids\": [str]}. unknowns and next_steps are lists of strings.",
        "",
        f"Finding: {finding.title}",
        f"Pattern: {finding.pattern}",
        "Edges:",
    ]
    for edge in finding.edges:
        lines.append(
            f"- record {edge.source_record_id}: {names.get(edge.from_entity_id, edge.from_entity_id)} "
            f"--{edge.relation}--> {names.get(edge.to_entity_id, edge.to_entity_id)} "
            f"(observed {edge.observed_at[:10]}, matched by {METHOD_LABELS[edge.match_method]}, source {edge.source_id})"
        )
    lines.append("Evidence:")
    for record_id, item in sorted(evidence.items()):
        if record_id in finding.evidence_ids:
            lines.append(f"- record_id {record_id} ({item.source_name}, {item.observed_at[:10]}): {item.summary}")
    return "\n".join(lines)


def parse_explanation(raw: str, mode: str) -> Explanation:
    """Parse the first JSON object in a model response. Raises ValueError if it is malformed."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model response")
    data = json.loads(raw[start : end + 1])

    def claims(key: str) -> tuple[Claim, ...]:
        return tuple(
            Claim(text=str(item["text"]), evidence_ids=tuple(str(x) for x in item.get("evidence_ids", [])))
            for item in data.get(key, [])
        )

    try:
        return Explanation(
            title=str(data["title"]),
            summary=str(data["summary"]),
            confirmed_facts=claims("confirmed_facts"),
            inferences=claims("inferences"),
            unknowns=tuple(str(x) for x in data.get("unknowns", [])),
            why_it_matters=str(data["why_it_matters"]),
            next_steps=tuple(str(x) for x in data.get("next_steps", [])),
            mode=mode,
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"missing or malformed field: {exc}") from exc


def _date_range(finding: Finding) -> str:
    dates = sorted(edge.observed_at[:10] for edge in finding.edges)
    return dates[0] if dates[0] == dates[-1] else f"{dates[0]} to {dates[-1]}"


def deterministic_explanation(finding: Finding, evidence: Mapping[str, Evidence], names: Mapping[str, str]) -> Explanation:
    """Template explanation built only from the finding's own edges and evidence."""
    target = names.get(finding.target_id, finding.target_id)

    confirmed = []
    for edge in finding.edges:
        item = evidence.get(edge.source_record_id)
        if item is None:
            continue
        confirmed.append(Claim(f"{item.source_name}, record {edge.source_record_id}, documents: {item.summary}", (edge.source_record_id,)))

    supplier_ids = sorted({path[0].from_entity_id for path in finding.paths})
    supplier_names = [names.get(eid, eid) for eid in supplier_ids]
    use_ids = tuple(sorted({edge.source_record_id for path in finding.paths for edge in path if edge.relation == "uses_facility"}))

    unknowns = [
        "These records do not show volumes, inventory, contract terms, or which parts pass through each facility, so the size of any impact is unknown.",
        "Records that could not be linked to exactly one entity were excluded, so related records may be missing.",
    ]
    for edge in finding.edges:
        if edge.match_method != "registry_id":
            unknowns.append(
                f"Record {edge.source_record_id} links entities by {METHOD_LABELS[edge.match_method]} only. "
                "A different entity with the same name and state would be linked incorrectly."
            )

    if finding.pattern == "shared_upstream":
        facility = names.get(finding.paths[0][1].to_entity_id, "a shared facility")
        summary = (
            f"{target} buys from {len(supplier_ids)} suppliers that each have a documented link to {facility}. "
            "Neither supplier's own disclosure shows this shared dependency."
        )
        inferences = (
            Claim(
                f"{join_names(supplier_names)} may share a dependency on {facility}. "
                "This is inferred from facility records and has not been confirmed as an operational dependency.",
                use_ids,
            ),
        )
        why = (
            f"If {facility} is disrupted, {len(supplier_ids)} suppliers of {target} could be affected at the same time, "
            "even though they look independent when reviewed one at a time."
        )
        next_steps = (
            f"Ask {target} and each supplier to confirm in writing whether {facility} processes any part they buy from the other supplier.",
            f"Ask the suppliers to name a second source for components that pass through {facility}.",
            f"Check the current registry status of {facility} before relying on it for continuity planning.",
        )
    else:
        notice = names.get(finding.paths[0][2].from_entity_id, "a notice")
        facility = names.get(finding.paths[0][2].to_entity_id, "a facility")
        summary = (
            f"{notice} applies to {facility}. {len(supplier_ids)} suppliers of {target} use that facility, "
            "so the notice may reach the supply chain for parts they buy. Blindspot found these links in documents only and did not confirm any operational impact."
        )
        inferences = (
            Claim(
                f"{notice} may affect {target}'s supply through {facility}. "
                "This is a two-hop inference from documents and has not been confirmed.",
                tuple(sorted(finding.evidence_ids)),
            ),
        )
        unknowns.append(f"The notice records are summaries. Their scope, duration, and whether they cover the parts {target} buys are not stated.")
        unknowns.append(f"Whether the notice is still in effect is unknown. The latest linked record is dated {_date_range(finding).split(' to ')[-1]}.")
        why = (
            f"A regulatory hold at a shared facility can slow or stop production for several suppliers at once. "
            f"{target} may learn of it late if it only tracks each supplier separately."
        )
        next_steps = (
            "Read the full notice text at its source URL and confirm its scope and status.",
            f"Ask {target}'s suppliers whether they were notified and whether any lots or shipments are affected.",
            "Watch the notice for updates or for the hold being lifted.",
        )

    return Explanation(
        title=finding.title,
        summary=summary,
        confirmed_facts=tuple(confirmed),
        inferences=inferences,
        unknowns=tuple(unknowns),
        why_it_matters=why,
        next_steps=next_steps,
        mode="deterministic_demo",
    )


class InvestigationAgent:
    """Explains findings. Uses a model client when one is configured, otherwise a deterministic template."""

    def __init__(self, client: ModelClient | None = None):
        self.client = client

    @property
    def mode_label(self) -> str:
        return "live model" if self.client is not None else "deterministic demo"

    def explain(self, finding: Finding, evidence: Mapping[str, Evidence], names: Mapping[str, str]) -> tuple[Explanation, list[str]]:
        notes: list[str] = []
        if self.client is not None:
            try:
                raw = self.client.complete(build_prompt(finding, evidence, names))
                candidate = parse_explanation(raw, mode="live_model")
            except ModelError as exc:
                notes.append(f"Live model call failed for '{finding.title}' ({exc}). Showing the deterministic explanation.")
            except ValueError as exc:
                notes.append(f"Model output for '{finding.title}' could not be parsed ({exc}). Showing the deterministic explanation.")
            else:
                errors = FindingValidator(finding, evidence).validate(candidate)
                if not errors:
                    return candidate, notes
                notes.append(
                    f"Model output for '{finding.title}' failed validation and was discarded: {'; '.join(errors)}. "
                    "Showing the deterministic explanation."
                )

        explanation = deterministic_explanation(finding, evidence, names)
        for error in FindingValidator(finding, evidence).validate(explanation):
            notes.append(f"Deterministic explanation for '{finding.title}' failed validation: {error}")
        return explanation, notes


def search_entities(entities: list[Entity], query: str) -> list[Entity]:
    """Match a search string against entity names, normalized names, and registry IDs."""
    raw = query.strip()
    if not raw:
        return []
    needle = normalize_name(raw)
    matches = [
        entity
        for entity in entities
        if raw.upper() == (entity.registry_id or "").upper() or (needle and needle in normalize_name(entity.name))
    ]
    return sorted(matches, key=lambda entity: (entity.kind != "company", entity.name))


def investigate(repository: Repository, target_id: str, agent: InvestigationAgent) -> InvestigationResult:
    graph = repository.load_graph()
    entities = graph.entity_map()
    if target_id not in entities:
        raise KeyError(f"Unknown entity: {target_id}")

    findings = RelationshipDiscovery(graph).find(target_id)
    names = {entity_id: entity.name for entity_id, entity in entities.items()}

    evidence_ids = {record_id for finding in findings for record_id in finding.evidence_ids}
    records = repository.load_records(sorted(evidence_ids))
    evidence = EvidenceRetriever(records, repository.source_names()).get(evidence_ids)

    result = InvestigationResult(
        target=entities[target_id],
        repository_mode=repository.mode,
        findings=findings,
        evidence=evidence,
        rejections=list(graph.rejections),
    )
    if not findings:
        result.notes.append(
            "No documented shared-upstream or event-exposure pattern was found for this entity in the loaded sources. "
            "This is not evidence that no relationship exists. It means these sources did not show one."
        )

    for finding in findings:
        explanation, notes = agent.explain(finding, evidence, names)
        result.explanations[finding.finding_id] = explanation
        result.notes.extend(notes)
    return result
