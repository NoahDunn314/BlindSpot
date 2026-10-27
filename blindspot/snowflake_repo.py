"""Snowflake implementation of the Repository protocol.

Reads the derived ENTITIES, RELATIONSHIPS, and REJECTED_MENTIONS tables, which
the SQL in snowflake/sql builds. Discovery then runs in Python over the same
Graph type that demo mode produces.

Failures raise SnowflakeError with a readable message. There is no silent
fallback to demo data.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from blindspot.adapters import SourceInfo
from blindspot.config import PROJECT_ROOT, Settings, dotenv_conflicts
from blindspot.models import Edge, Entity, Graph, InvestigationResult, Rejection, SourceRecord

try:
    import snowflake.connector as sf
    from snowflake.connector.errors import Error as SnowflakeDatabaseError
except ImportError:  # The connector is optional. Demo mode must work without it.
    sf = None
    SnowflakeDatabaseError = Exception

SQL_DIR = PROJECT_ROOT / "snowflake" / "sql"
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class SnowflakeError(RuntimeError):
    """A readable Snowflake configuration, connection, or query error."""


def _to_naive_utc(iso_text: str) -> datetime:
    parsed = datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _to_iso(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).strftime(TIMESTAMP_FORMAT)
    return str(value)


def connect(settings: Settings, with_database: bool = True):
    """Open a connection from settings. Missing configuration is reported by variable name.

    with_database=False is used only by the setup script, which creates the database.
    """
    if sf is None:
        raise SnowflakeError("snowflake-connector-python is not installed. Run: pip install -r requirements-snowflake.txt")

    required = {
        "SNOWFLAKE_ACCOUNT": settings.snowflake_account,
        "SNOWFLAKE_USER": settings.snowflake_user,
        "SNOWFLAKE_WAREHOUSE": settings.snowflake_warehouse,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SnowflakeError(f"Snowflake mode needs these variables set in .env or the environment: {', '.join(missing)}")
    if not settings.snowflake_password and not settings.snowflake_authenticator:
        raise SnowflakeError(
            "Snowflake authentication is not configured. Set SNOWFLAKE_PASSWORD or "
            "SNOWFLAKE_AUTHENTICATOR (for example, externalbrowser)."
        )

    kwargs = {
        "account": settings.snowflake_account,
        "user": settings.snowflake_user,
        "warehouse": settings.snowflake_warehouse,
        "login_timeout": 15,
    }
    if with_database:
        kwargs["database"] = settings.snowflake_database
        kwargs["schema"] = settings.snowflake_schema
    if settings.snowflake_password:
        kwargs["password"] = settings.snowflake_password
    if settings.snowflake_authenticator:
        kwargs["authenticator"] = settings.snowflake_authenticator
    if settings.snowflake_role:
        kwargs["role"] = settings.snowflake_role

    try:
        return sf.connect(**kwargs)
    except SnowflakeDatabaseError as exc:
        conflicts = [name for name in dotenv_conflicts() if name.startswith("SNOWFLAKE_")]
        hint = (
            f" Note: {', '.join(conflicts)} in your environment differs from .env. An editor or shell that loads .env itself "
            "may have cut a value at a '#'. Put the value in double quotes in .env and restart the terminal."
            if conflicts
            else ""
        )
        raise SnowflakeError(f"Could not connect to Snowflake account {settings.snowflake_account}: {exc}{hint}") from exc


class SnowflakeRepository:
    mode = "snowflake"

    def __init__(self, connection):
        self._conn = connection

    @classmethod
    def from_settings(cls, settings: Settings) -> "SnowflakeRepository":
        return cls(connect(settings))

    def close(self) -> None:
        self._conn.close()

    def _rows(self, sql: str, params: tuple | None = None) -> list[tuple]:
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql, params)
            return cursor.fetchall()
        except SnowflakeDatabaseError as exc:
            raise SnowflakeError(f"Snowflake query failed: {exc}") from exc
        finally:
            cursor.close()

    def _run(self, sql: str, params=None) -> None:
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql, params)
        except SnowflakeDatabaseError as exc:
            raise SnowflakeError(f"Snowflake statement failed: {exc}") from exc
        finally:
            cursor.close()

    # Read path used by the investigation workflow

    def load_graph(self) -> Graph:
        entities = tuple(
            Entity(entity_id=row[0], kind=row[1], name=row[2], registry_id=row[3], state=row[4])
            for row in self._rows(
                "SELECT ENTITY_ID, ENTITY_KIND, NAME, REGISTRY_ID, STATE_CODE FROM ENTITIES ORDER BY ENTITY_ID"
            )
        )
        edges = tuple(
            Edge(
                edge_id=row[0],
                from_entity_id=row[1],
                to_entity_id=row[2],
                relation=row[3],
                status=row[4],
                source_id=row[5],
                source_record_id=row[6],
                source_url=row[7] or "",
                observed_at=_to_iso(row[8]),
                match_method=row[9],
                match_rationale=row[10] or "",
            )
            for row in self._rows(
                """
                SELECT RELATIONSHIP_ID, FROM_ENTITY_ID, TO_ENTITY_ID, RELATION_TYPE, STATUS, SOURCE_ID,
                       SOURCE_RECORD_ID, SOURCE_URL, OBSERVED_AT, MATCH_METHOD, MATCH_RATIONALE
                FROM RELATIONSHIPS
                ORDER BY RELATIONSHIP_ID
                """
            )
        )
        rejections = tuple(
            Rejection(record_id=row[0], mention_name=row[1], reason=row[2])
            for row in self._rows("SELECT RECORD_ID, MENTION_NAME, REASON FROM REJECTED_MENTIONS ORDER BY RECORD_ID, MENTION_NAME")
        )
        return Graph(entities=entities, edges=edges, rejections=rejections)

    def load_records(self, record_ids: list[str]) -> list[SourceRecord]:
        if not record_ids:
            return []
        placeholders = ", ".join(["%s"] * len(record_ids))
        rows = self._rows(
            f"""
            SELECT RECORD_ID, SOURCE_ID, RECORD_TYPE, OBSERVED_AT, SOURCE_URL, PAYLOAD, IS_SYNTHETIC
            FROM SOURCE_RECORDS
            WHERE RECORD_ID IN ({placeholders})
            ORDER BY RECORD_ID
            """,
            tuple(record_ids),
        )
        return [
            SourceRecord(
                record_id=row[0],
                source_id=row[1],
                record_type=row[2],
                observed_at=_to_iso(row[3]),
                url=row[4] or "",
                payload=json.loads(row[5]) if isinstance(row[5], str) else row[5],
                is_synthetic=bool(row[6]),
            )
            for row in rows
        ]

    def source_names(self) -> dict[str, str]:
        return {row[0]: row[1] for row in self._rows("SELECT SOURCE_ID, SOURCE_NAME FROM SOURCES")}

    def has_synthetic_records(self) -> bool:
        return bool(self._rows("SELECT 1 FROM SOURCE_RECORDS WHERE IS_SYNTHETIC LIMIT 1"))

    # Write path used by the loader and by investigation saving

    def execute_script(self, sql_text: str) -> None:
        # execute_string lives on the connection, not the cursor, in snowflake-connector-python.
        try:
            for _ in self._conn.execute_string(sql_text):
                pass
        except SnowflakeDatabaseError as exc:
            raise SnowflakeError(f"Snowflake script failed: {exc}") from exc

    def run_script_file(self, name: str) -> None:
        self.execute_script((SQL_DIR / name).read_text(encoding="utf-8"))

    def stage_sources(self, infos: Iterable[SourceInfo]) -> None:
        rows = [
            (i.source_id, i.name, i.access, i.licensing, i.update_cadence, i.matching_limitations, True)
            for i in infos
        ]
        self._run("DELETE FROM SOURCES")
        cursor = self._conn.cursor()
        try:
            cursor.executemany(
                """
                INSERT INTO SOURCES (SOURCE_ID, SOURCE_NAME, ACCESS_NOTES, LICENSING, UPDATE_CADENCE,
                                     MATCHING_LIMITATIONS, IS_SYNTHETIC)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )
        except SnowflakeDatabaseError as exc:
            raise SnowflakeError(f"Could not load SOURCES: {exc}") from exc
        finally:
            cursor.close()

    def stage_records(self, records: Iterable[SourceRecord]) -> int:
        rows = [
            (
                r.record_id,
                r.source_id,
                r.record_type,
                _to_naive_utc(r.observed_at),
                r.url,
                json.dumps(r.payload, sort_keys=True),
                r.is_synthetic,
            )
            for r in records
        ]
        self._run("TRUNCATE TABLE STG_SOURCE_RECORDS")
        cursor = self._conn.cursor()
        try:
            cursor.executemany(
                """
                INSERT INTO STG_SOURCE_RECORDS
                    (RECORD_ID, SOURCE_ID, RECORD_TYPE, OBSERVED_AT, SOURCE_URL, PAYLOAD_JSON, IS_SYNTHETIC)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )
        except SnowflakeDatabaseError as exc:
            raise SnowflakeError(f"Could not stage source records: {exc}") from exc
        finally:
            cursor.close()
        return len(rows)

    def run_checks(self) -> list[tuple[str, int]]:
        """Run snowflake/sql/04_checks.sql and return (check name, violation count) pairs."""
        sql_text = (SQL_DIR / "04_checks.sql").read_text(encoding="utf-8")
        rows: list[tuple] = []
        try:
            for statement_cursor in self._conn.execute_string(sql_text):
                if statement_cursor.description:
                    rows = statement_cursor.fetchall()
        except SnowflakeDatabaseError as exc:
            raise SnowflakeError(f"Snowflake checks failed: {exc}") from exc
        return [(row[0], int(row[1])) for row in rows]

    def save_investigation(self, result: InvestigationResult) -> str:
        investigation_id = uuid4().hex
        self._run(
            """
            INSERT INTO INVESTIGATIONS (INVESTIGATION_ID, TARGET_ENTITY_ID, REPOSITORY_MODE, CREATED_AT, NOTES)
            SELECT %s, %s, %s, CURRENT_TIMESTAMP(), PARSE_JSON(%s)
            """,
            (investigation_id, result.target.entity_id, result.repository_mode, json.dumps(result.notes)),
        )
        for finding in result.findings:
            explanation = result.explanations.get(finding.finding_id)
            self._run(
                """
                INSERT INTO FINDINGS
                    (INVESTIGATION_ID, FINDING_ID, PATTERN, TITLE, SCORE, CONFIDENCE, EVIDENCE_IDS, EXPLANATION)
                SELECT %s, %s, %s, %s, %s, %s, PARSE_JSON(%s), PARSE_JSON(%s)
                """,
                (
                    investigation_id,
                    finding.finding_id,
                    finding.pattern,
                    finding.title,
                    finding.score,
                    finding.confidence,
                    json.dumps(list(finding.evidence_ids)),
                    json.dumps(asdict(explanation)) if explanation else "null",
                ),
            )
        return investigation_id
