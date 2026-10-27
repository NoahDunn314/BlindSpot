"""Runtime settings from environment variables and an optional .env file.

Secrets are read from the environment only. Nothing in this repository stores them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEMO_FIXTURE_DIR = PROJECT_ROOT / "fixtures" / "demo"
DEFAULT_MODEL = "claude-sonnet-5-5"


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Load KEY=VALUE pairs from a .env file without overriding real environment variables."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    mode: str  # "demo" or "snowflake"
    snowflake_account: str | None
    snowflake_user: str | None
    snowflake_password: str | None
    snowflake_authenticator: str | None
    snowflake_warehouse: str | None
    snowflake_role: str | None
    snowflake_database: str
    snowflake_schema: str
    anthropic_api_key: str | None
    model: str


def load_settings(env: dict | None = None) -> Settings:
    source = os.environ if env is None else env
    mode = (source.get("BLINDSPOT_MODE") or "demo").strip().lower()
    if mode not in {"demo", "snowflake"}:
        raise ValueError(f"BLINDSPOT_MODE must be 'demo' or 'snowflake', not {mode!r}.")

    def value(name: str) -> str | None:
        return (source.get(name) or "").strip() or None

    return Settings(
        mode=mode,
        snowflake_account=value("SNOWFLAKE_ACCOUNT"),
        snowflake_user=value("SNOWFLAKE_USER"),
        snowflake_password=value("SNOWFLAKE_PASSWORD"),
        snowflake_authenticator=value("SNOWFLAKE_AUTHENTICATOR"),
        snowflake_warehouse=value("SNOWFLAKE_WAREHOUSE"),
        snowflake_role=value("SNOWFLAKE_ROLE"),
        snowflake_database=value("SNOWFLAKE_DATABASE") or "BLINDSPOT",
        snowflake_schema=value("SNOWFLAKE_SCHEMA") or "CORE",
        anthropic_api_key=value("ANTHROPIC_API_KEY"),
        model=value("BLINDSPOT_MODEL") or DEFAULT_MODEL,
    )
