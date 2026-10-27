"""Compare the demo-mode graph and discovery output with the Snowflake-mode output.

Usage:
    python scripts/check_parity.py

Passes when entities, edges, rejections, and findings match. This is the check
that keeps the Python resolver and the SQL views in step. Run it after changing
either one. It needs a loaded Snowflake account.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blindspot.config import load_dotenv, load_settings  # noqa: E402
from blindspot.discovery import RelationshipDiscovery  # noqa: E402
from blindspot.models import Graph  # noqa: E402
from blindspot.repository import DemoRepository  # noqa: E402
from blindspot.snowflake_repo import SnowflakeError, SnowflakeRepository  # noqa: E402


def graph_signature(graph: Graph) -> dict[str, list]:
    return {
        "entities": sorted((e.entity_id, e.kind, e.name, e.registry_id or "", e.state or "") for e in graph.entities),
        "edges": sorted(
            (e.edge_id, e.from_entity_id, e.to_entity_id, e.relation, e.status, e.source_id, e.match_method, e.observed_at)
            for e in graph.edges
        ),
        "rejections": sorted((r.record_id, r.mention_name) for r in graph.rejections),
    }


def finding_signature(graph: Graph) -> list:
    discovery = RelationshipDiscovery(graph)
    signature = []
    for entity in graph.entities:
        if entity.kind == "company":
            for finding in discovery.find(entity.entity_id):
                signature.append((entity.entity_id, finding.finding_id, finding.score, finding.confidence))
    return sorted(signature)


def main() -> int:
    load_dotenv()
    settings = load_settings()
    try:
        snowflake_repo = SnowflakeRepository.from_settings(settings)
    except SnowflakeError as exc:
        print(f"ERROR: {exc}")
        return 1

    try:
        demo_graph = DemoRepository().load_graph()
        live_graph = snowflake_repo.load_graph()
    except SnowflakeError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        snowflake_repo.close()

    failures = []
    demo_parts, live_parts = graph_signature(demo_graph), graph_signature(live_graph)
    for part in demo_parts:
        if demo_parts[part] != live_parts[part]:
            failures.append(f"{part} differ")
    if finding_signature(demo_graph) != finding_signature(live_graph):
        failures.append("findings differ")

    if failures:
        print("PARITY FAILED:", "; ".join(failures))
        return 1
    print("PARITY OK: entities, edges, rejections, and findings match between demo and Snowflake.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
