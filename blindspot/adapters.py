"""Source adapters. Each adapter turns one source into normalized SourceRecord objects.

To add a source, implement the DataSourceAdapter protocol: provide an `info`
attribute describing access and licensing, and a `fetch()` method that yields
SourceRecord objects with stable record IDs and original URLs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from blindspot.config import DEMO_FIXTURE_DIR
from blindspot.models import SourceRecord


@dataclass(frozen=True)
class SourceInfo:
    source_id: str
    name: str
    access: str
    credentials_required: bool
    cost: str
    licensing: str
    update_cadence: str
    matching_limitations: str


class DataSourceAdapter(Protocol):
    info: SourceInfo

    def fetch(self) -> Iterable[SourceRecord]:
        """Yield normalized records. Must be deterministic for the same input."""
        ...


class FixtureAdapter:
    """Reads a deterministic, clearly labeled JSON fixture. Used by demo mode and tests."""

    def __init__(self, info: SourceInfo, path: Path):
        self.info = info
        self.path = path

    def fetch(self) -> Iterable[SourceRecord]:
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        for row in rows:
            yield SourceRecord(
                record_id=row["record_id"],
                source_id=self.info.source_id,
                record_type=row["record_type"],
                observed_at=row["observed_at"],
                url=row["url"],
                payload=row["payload"],
                is_synthetic=True,
            )


def demo_adapters(fixture_dir: Path = DEMO_FIXTURE_DIR) -> list[DataSourceAdapter]:
    """Build one fixture adapter per source listed in fixtures/demo/sources.json."""
    entries = json.loads((fixture_dir / "sources.json").read_text(encoding="utf-8"))
    adapters: list[DataSourceAdapter] = []
    for entry in entries:
        info = SourceInfo(
            source_id=entry["source_id"],
            name=entry["name"],
            access=entry["access"],
            credentials_required=entry["credentials_required"],
            cost=entry["cost"],
            licensing=entry["licensing"],
            update_cadence=entry["update_cadence"],
            matching_limitations=entry["matching_limitations"],
        )
        adapters.append(FixtureAdapter(info, fixture_dir / entry["file"]))
    return adapters


def ingest(adapters: Iterable[DataSourceAdapter]) -> list[SourceRecord]:
    """Fetch every adapter and return records sorted by ID.

    Records are keyed by record_id, so re-running ingestion over the same
    sources produces the same result (idempotent).
    """
    by_id: dict[str, SourceRecord] = {}
    for adapter in adapters:
        for record in adapter.fetch():
            by_id[record.record_id] = record
    return [by_id[key] for key in sorted(by_id)]
