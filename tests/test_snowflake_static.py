"""Static checks on the Snowflake SQL. These run without an account.

Live parity between SQL and Python is checked by scripts/check_parity.py.
"""

import re

from blindspot.config import PROJECT_ROOT
from blindspot.resolution import LEGAL_SUFFIXES

SQL_DIR = PROJECT_ROOT / "snowflake" / "sql"


def test_sql_suffix_list_matches_python_resolver():
    views = (SQL_DIR / "02_views.sql").read_text(encoding="utf-8")
    match = re.search(r"\(\^\| \)\((.+?)\)\$", views)
    assert match, "NORMALIZE_NAME suffix pattern not found in 02_views.sql"
    assert set(match.group(1).split("|")) == LEGAL_SUFFIXES


def test_expected_sql_files_exist():
    for name in ["01_schema.sql", "02_views.sql", "03_refresh.sql", "04_checks.sql"]:
        assert (SQL_DIR / name).is_file(), name


def test_no_credentials_in_sql_or_env_example():
    for path in list(SQL_DIR.glob("*.sql")) + [PROJECT_ROOT / ".env.example"]:
        text = path.read_text(encoding="utf-8").lower()
        assert "password=" not in text.replace("snowflake_password=", ""), path.name
