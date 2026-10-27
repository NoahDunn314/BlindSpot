"""SnowflakeRepository tested against a fake connection. No Snowflake account is needed.

These tests cover the Python side only: row mapping, parameter handling, error wrapping.
They cannot validate the SQL. Use scripts/load_snowflake.py and scripts/check_parity.py for that.
"""

import json
from datetime import datetime

import pytest

from blindspot import snowflake_repo
from blindspot.config import load_settings
from blindspot.investigation import InvestigationAgent, investigate
from blindspot.models import SourceRecord
from blindspot.repository import DemoRepository
from blindspot.snowflake_repo import SnowflakeDatabaseError, SnowflakeError, SnowflakeRepository


class FakeCursor:
    description = [("col",)]

    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def execute(self, sql, params=None):
        self.conn.calls.append(("execute", " ".join(sql.split()), params))
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise SnowflakeDatabaseError("boom")
        self._rows = next((rows for key, rows in self.conn.results.items() if key in sql), [])

    def executemany(self, sql, rows):
        self.conn.calls.append(("executemany", " ".join(sql.split()), list(rows)))

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConnection:
    def __init__(self, results=None, fail_on=None):
        self.calls = []
        self.results = results or {}
        self.fail_on = fail_on

    def cursor(self):
        return FakeCursor(self)

    def execute_string(self, sql_text):
        self.calls.append(("execute_string", sql_text[:30], None))
        cursor = FakeCursor(self)
        cursor._rows = self.results.get("checks", [])
        yield cursor

    def close(self):
        pass


def test_load_graph_maps_rows_and_formats_timestamps():
    conn = FakeConnection(
        {
            "FROM ENTITIES": [("e1", "company", "Acme", "A-1", "OH")],
            "FROM RELATIONSHIPS": [
                ("R-1", "e1", "e1", "supplies_to", "documented", "src", "R-1", "u", datetime(2026, 3, 2), "registry_id", None)
            ],
            "FROM REJECTED_MENTIONS": [("R-9", "Pinecrest", "ambiguous match")],
        }
    )
    graph = SnowflakeRepository(conn).load_graph()
    assert graph.entities[0].name == "Acme"
    assert graph.edges[0].observed_at == "2026-03-02T00:00:00Z"
    assert graph.edges[0].match_rationale == ""
    assert graph.rejections[0].record_id == "R-9"


def test_load_records_skips_query_for_no_ids_and_parses_json_payload():
    conn = FakeConnection({"FROM SOURCE_RECORDS": [("SD-1", "s", "relationship", datetime(2026, 3, 2), "u", '{"a": 1}', True)]})
    repo = SnowflakeRepository(conn)
    assert repo.load_records([]) == []
    assert conn.calls == []
    records = repo.load_records(["SD-1"])
    assert records[0].payload == {"a": 1}
    assert conn.calls[0][2] == ("SD-1",)
    assert "IN (%s)" in conn.calls[0][1]


def test_query_failure_is_wrapped_in_readable_error():
    repo = SnowflakeRepository(FakeConnection(fail_on="ENTITIES"))
    with pytest.raises(SnowflakeError, match="Snowflake query failed"):
        repo.load_graph()


def test_stage_records_truncates_then_inserts_naive_utc_and_sorted_json():
    conn = FakeConnection()
    record = SourceRecord("R-1", "s", "relationship", "2026-03-02T00:00:00Z", "u", {"b": 1, "a": 2})
    assert SnowflakeRepository(conn).stage_records([record]) == 1
    assert "TRUNCATE" in conn.calls[0][1]
    row = conn.calls[1][2][0]
    assert row[3] == datetime(2026, 3, 2) and row[3].tzinfo is None
    assert row[5] == '{"a": 2, "b": 1}'


def test_run_checks_returns_name_and_count_pairs():
    conn = FakeConnection({"checks": [("orphans", 0), ("dupes", 2)]})
    assert SnowflakeRepository(conn).run_checks() == [("orphans", 0), ("dupes", 2)]


def test_save_investigation_writes_one_investigation_and_one_row_per_finding():
    result = investigate(DemoRepository(), "ent-harborline", InvestigationAgent())
    conn = FakeConnection()
    investigation_id = SnowflakeRepository(conn).save_investigation(result)

    inserts = [call for call in conn.calls if call[0] == "execute"]
    assert len(inserts) == 1 + len(result.findings)
    assert "INSERT INTO INVESTIGATIONS" in inserts[0][1]
    assert inserts[0][2][0] == investigation_id
    finding_params = inserts[1][2]
    assert finding_params[1] == result.findings[0].finding_id
    assert json.loads(finding_params[7])["mode"] == "deterministic_demo"


@pytest.mark.skipif(snowflake_repo.sf is None, reason="snowflake-connector-python not installed")
def test_connect_names_every_missing_variable():
    with pytest.raises(SnowflakeError) as error:
        snowflake_repo.connect(load_settings({"BLINDSPOT_MODE": "snowflake"}))
    for name in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_WAREHOUSE"):
        assert name in str(error.value)


@pytest.mark.skipif(snowflake_repo.sf is None, reason="snowflake-connector-python not installed")
def test_connect_requires_an_authentication_method():
    settings = load_settings(
        {
            "SNOWFLAKE_ACCOUNT": "account",
            "SNOWFLAKE_USER": "user",
            "SNOWFLAKE_WAREHOUSE": "warehouse",
        }
    )
    with pytest.raises(SnowflakeError, match="SNOWFLAKE_PASSWORD or SNOWFLAKE_AUTHENTICATOR"):
        snowflake_repo.connect(settings)
