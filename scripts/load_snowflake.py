"""Create the Blindspot schema in Snowflake, load the demo fixtures, and refresh derived tables.

Usage:
    python scripts/load_snowflake.py

Requires SNOWFLAKE_* variables in .env (see .env.example) and snowflake-connector-python.
Every loaded record is a SYNTHETIC demo fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blindspot.adapters import demo_adapters, ingest  # noqa: E402
from blindspot.config import load_dotenv, load_settings  # noqa: E402
from blindspot.snowflake_repo import SnowflakeError, SnowflakeRepository, connect  # noqa: E402


def main() -> int:
    load_dotenv()
    settings = load_settings()

    adapters = demo_adapters()
    records = ingest(adapters)

    try:
        # The setup connection has no database yet. 01_schema.sql creates it.
        repo = SnowflakeRepository(connect(settings, with_database=False))
        try:
            print("Creating schema and views...")
            repo.run_script_file("01_schema.sql")
            repo.run_script_file("02_views.sql")
            repo.stage_sources([adapter.info for adapter in adapters])
            staged = repo.stage_records(records)
            print(f"Staged {staged} SYNTHETIC demo records. Refreshing derived tables...")
            repo.run_script_file("03_refresh.sql")
            checks = repo.run_checks()
        finally:
            repo.close()
    except SnowflakeError as exc:
        print(f"ERROR: {exc}")
        return 1

    print("Integrity checks (VIOLATIONS should be 0):")
    for name, violations in checks:
        print(f"  {name}: {violations}")
    failed = [name for name, violations in checks if violations]
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print("Snowflake load complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
