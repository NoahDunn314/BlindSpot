"""Entity resolution: link a name mentioned in a source record to one reference entity.

Rules, strongest first:
  1. registry_id match                 -> method "registry_id"
  2. name + state match (unique)       -> method "name_and_state"
  3. name only, one candidate, no state -> method "name_only" (weak)
Anything else is rejected with a reason. Ambiguous or conflicting matches are
never linked. Snowflake's V_RESOLVED_MENTIONS view mirrors these rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from blindspot.models import Entity

# Only one trailing legal-form suffix is removed. Keep this in sync with 02_views.sql.
LEGAL_SUFFIXES = {"inc", "llc", "ltd", "co", "corp", "corporation", "company", "limited"}

METHOD_RANK = {"registry_id": 3, "name_and_state": 2, "name_only": 1}


def normalize_name(name: str) -> str:
    tokens = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    if tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


@dataclass(frozen=True)
class Resolution:
    entity_id: str | None
    method: str | None
    reason: str


class EntityResolver:
    def __init__(self, entities: Iterable[Entity]):
        self._entities = sorted(entities, key=lambda entity: entity.entity_id)

    def resolve(self, mention: dict) -> Resolution:
        name = mention.get("name") or ""
        registry_id = mention.get("registry_id")
        state = mention.get("state")

        if registry_id:
            hits = [e for e in self._entities if e.registry_id == registry_id]
            if len(hits) == 1:
                return Resolution(hits[0].entity_id, "registry_id", f"registry ID {registry_id} matches {hits[0].entity_id}")
            return Resolution(None, None, f"registry ID {registry_id} is not in reference data")

        candidates = [e for e in self._entities if normalize_name(e.name) == normalize_name(name)]
        if not normalize_name(name):
            # Names written only in non-Latin script normalize to "". Never match on an empty name.
            return Resolution(None, None, f"name '{name}' has no matchable Latin characters")
        if not candidates:
            return Resolution(None, None, f"no reference entity named '{name}'")

        if state:
            in_state = [e for e in candidates if e.state == state]
            if len(in_state) == 1:
                return Resolution(in_state[0].entity_id, "name_and_state", f"name and state {state} match {in_state[0].entity_id}")
            if not in_state:
                return Resolution(None, None, f"name matches but no candidate is in state {state}")
            return Resolution(None, None, f"name and state {state} match {len(in_state)} entities (ambiguous)")

        if len(candidates) == 1:
            return Resolution(candidates[0].entity_id, "name_only", f"name only, matches {candidates[0].entity_id}")
        return Resolution(None, None, f"name matches {len(candidates)} entities with no state or registry ID (ambiguous)")
