"""The Claude Code worker queue, tested with a thread standing in for the Claude Code session."""

import json
import threading
import time

import pytest

from blindspot import worker
from blindspot.config import load_settings
from blindspot.investigation import InvestigationAgent, ModelError, investigate
from blindspot.repository import DemoRepository
from blindspot.worker import ClaudeCodeWorkerClient


def _valid_reply(allowed_ids):
    first = allowed_ids[0]
    return json.dumps(
        {
            "title": "Worker explanation",
            "summary": "Written by the worker from the evidence.",
            "confirmed_facts": [{"text": f"Record {first} documents a link.", "evidence_ids": [first]}],
            "inferences": [{"text": "The link may matter.", "evidence_ids": list(allowed_ids)}],
            "unknowns": ["Volumes are unknown."],
            "why_it_matters": "Possible shared dependency.",
            "next_steps": ["Ask the suppliers."],
        }
    )


class FakeWorker(threading.Thread):
    """Plays Claude Code: listens, claims a job, and replies with a valid explanation."""

    def __init__(self):
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.answered = []

    def run(self):
        while not self.stop.is_set():
            worker.beat()
            job = worker.claim_next_job()
            if job:
                assert worker.submit_reply(job["id"], _valid_reply(job["allowed_ids"])) == []
                self.answered.append(job["id"])
            time.sleep(0.05)


def test_no_listener_raises_immediately_so_the_app_falls_back_without_waiting():
    started = time.time()
    with pytest.raises(ModelError, match="no Claude Code worker is listening"):
        ClaudeCodeWorkerClient(timeout=30).complete("prompt")
    assert time.time() - started < 2


def test_end_to_end_findings_are_explained_by_the_worker_and_labeled():
    fake = FakeWorker()
    fake.start()
    try:
        result = investigate(DemoRepository(), "ent-harborline", InvestigationAgent(ClaudeCodeWorkerClient(timeout=15)))
    finally:
        fake.stop.set()
    assert {e.mode for e in result.explanations.values()} == {"claude_code_worker"}
    assert len(fake.answered) == len(result.findings) == 2
    assert not any("failed" in note for note in result.notes)


def test_reply_citing_unknown_evidence_is_rejected_and_the_job_stays_open_for_a_fix():
    job_id = worker.submit_job("prompt", ["SD-001"])
    worker.claim_next_job()
    bad = json.loads(_valid_reply(["SD-001"]))
    bad["confirmed_facts"] = [{"text": "Invented.", "evidence_ids": ["SD-999"]}]
    errors = worker.submit_reply(job_id, json.dumps(bad))
    assert any("outside this finding" in error for error in errors)
    assert worker.wait_for_reply(job_id, timeout=0.2) is None
    assert worker.submit_reply(job_id, _valid_reply(["SD-001"])) == []  # fixed and resent


def test_reply_with_a_percentage_is_rejected():
    job_id = worker.submit_job("prompt", ["SD-001"])
    worker.claim_next_job()
    bad = json.loads(_valid_reply(["SD-001"]))
    bad["summary"] = "There is a 40% chance of disruption."
    assert any("numeric probability" in e for e in worker.submit_reply(job_id, json.dumps(bad)))


def test_malformed_reply_and_invalid_job_ids_are_rejected():
    job_id = worker.submit_job("prompt", ["SD-001"])
    worker.claim_next_job()
    assert worker.submit_reply(job_id, "not json")
    assert worker.submit_reply("../../etc/passwd", "{}")
    assert worker.submit_reply("0123456789abcdef", "{}")  # well-formed id, but no such claimed job


def test_a_job_can_only_be_claimed_once():
    worker.submit_job("prompt", [])
    assert worker.claim_next_job() is not None
    assert worker.claim_next_job() is None


def test_timeout_cancels_the_job_and_the_agent_falls_back_with_a_visible_note():
    worker.beat()  # looks available, but nobody ever claims the job
    result = investigate(DemoRepository(), "ent-harborline", InvestigationAgent(ClaudeCodeWorkerClient(timeout=0.6)))
    assert all(e.mode == "deterministic_demo" for e in result.explanations.values())
    assert any("did not reply" in note for note in result.notes)
    assert worker.pending_jobs() == 0


def test_availability_covers_listening_working_and_the_gap_after_a_reply():
    assert not worker.worker_available()
    worker.beat()
    assert worker.worker_available()
    worker.stop_beat()
    assert not worker.worker_available()
    worker.submit_job("prompt", [])
    worker.claim_next_job()  # a claimed job in flight counts as a worker at work
    assert worker.worker_available()


def test_provider_setting_is_validated_and_defaults_from_the_api_key():
    assert load_settings({"BLINDSPOT_PROVIDER": "claude_code"}).model_provider == "claude_code"
    assert load_settings({"ANTHROPIC_API_KEY": "k"}).model_provider == "anthropic"
    assert load_settings({}).model_provider is None
    with pytest.raises(ValueError):
        load_settings({"BLINDSPOT_PROVIDER": "other"})


def test_ui_with_worker_provider_shows_waiting_banner_then_worker_labeled_explanations(monkeypatch):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    app = str(Path(__file__).resolve().parent.parent / "app.py")
    monkeypatch.setenv("BLINDSPOT_PROVIDER", "claude_code")

    # no worker yet: the page says so and the investigation still completes with the labeled demo template
    at = AppTest.from_file(app, default_timeout=60).run()
    assert not at.exception
    assert any("No Claude Code worker is listening" in w.value for w in at.warning)
    next(b for b in at.button if b.key == "sample-ent-harborline").click().run()
    assert any("deterministic demo template" in c.value for c in at.caption)

    # worker listening: both findings are explained by it and the caption says so
    fake = FakeWorker()
    fake.start()
    try:
        at = AppTest.from_file(app, default_timeout=60).run()
        assert any("worker is listening" in s.value for s in at.success)
        next(b for b in at.button if b.key == "sample-ent-harborline").click().run()
    finally:
        fake.stop.set()
    assert not at.exception
    assert any("Claude Code worker session" in c.value for c in at.caption)
    assert len(fake.answered) == 2
