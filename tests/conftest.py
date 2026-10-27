import pytest


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch, tmp_path):
    """Keep tests independent of the developer's .env.

    load_dotenv() never overrides variables that are already set, so setting them here wins.
    Without this, a .env with BLINDSPOT_MODE=snowflake would send the UI tests to a real account,
    and an ANTHROPIC_API_KEY or BLINDSPOT_PROVIDER would make them call a live model or wait on a worker.
    """
    monkeypatch.setenv("BLINDSPOT_MODE", "demo")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("BLINDSPOT_PROVIDER", "")
    monkeypatch.setenv("BLINDSPOT_WORKER_DIR", str(tmp_path / "worker"))
