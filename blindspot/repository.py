"""Data access interface and the demo implementation.

Both modes implement the same Repository protocol, so the investigation workflow
does not know which one it is using. The Snowflake implementation lives in
blindspot/snowflake_repo.py and is imported only when Snowflake mode is selected.
"""

from __future__ import annotations

from typing import Iterable, Protocol

from blindspot.adapters import DataSourceAdapter, demo_adapters, ingest
from blindspot.config import Settings
from blindspot.discovery import build_graph
from blindspot.models import Graph, SourceRecord


class Repository(Protocol):
    mode: str  # "demo" or "snowflake"

    def load_graph(self) -> Graph:
        """Return resolved entities, documented edges, and rejected mentions."""
        ...

    def load_records(self, record_ids: list[str]) -> list[SourceRecord]:
        """Return the normalized source records with these IDs (used for evidence)."""
        ...

    def source_names(self) -> dict[str, str]:
        """Map source_id to a display name."""
        ...


class DemoRepository:
    """Runs ingestion and discovery in memory over the synthetic fixtures."""

    mode = "demo"

    def __init__(self, adapters: Iterable[DataSourceAdapter] | None = None):
        self._adapters = list(adapters) if adapters is not None else demo_adapters()
        self._records = ingest(self._adapters)
        self._graph = build_graph(self._records)

    def load_graph(self) -> Graph:
        return self._graph

    def load_records(self, record_ids: list[str]) -> list[SourceRecord]:
        wanted = set(record_ids)
        return [record for record in self._records if record.record_id in wanted]

    def source_names(self) -> dict[str, str]:
        return {adapter.info.source_id: adapter.info.name for adapter in self._adapters}


def open_repository(settings: Settings) -> Repository:
    if settings.mode == "snowflake":
        from blindspot.snowflake_repo import SnowflakeRepository

        return SnowflakeRepository.from_settings(settings)
    return DemoRepository()
