"""A Claude Code session acting as a listening worker for the explanation layer.

The pattern is borrowed from the ai-tutor project (https://gitlab.com/hutizaki/ai-tutor): the web app
and Claude Code never talk directly. They share a small store on disk. The app writes a job. Claude
Code runs a blocking `listen` command in the background (waiting costs nothing), wakes when a job
arrives, analyzes it, and answers with a `reply` command. A heartbeat file tells the app whether a
worker is listening.

Here the job is the evidence for one finding. The reply is an Explanation as JSON. It goes through the
same parser and validator as any other model output, so the worker cannot invent a citation.
The worker is another ModelClient. Discovery still decides what is connected.

Layout under the worker directory (data/worker by default, override with BLINDSPOT_WORKER_DIR):
    inbox/<id>.json    submitted by the app, waiting for a worker
    claimed/<id>.json  taken by a worker, waiting for its reply
    outbox/<id>.json   the validated reply, waiting for the app
    listener.beat      touched while a worker is blocked in `listen`
    activity           touched on every claim and reply
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

from blindspot.config import PROJECT_ROOT
from blindspot.evidence import validate_explanation
from blindspot.investigation import ModelError, evidence_ids_in_prompt, parse_explanation

MODE = "claude_code_worker"
BEAT_FRESH_SECONDS = 8  # a listener is "listening" if it touched its heartbeat this recently
ACTIVITY_GRACE_SECONDS = 20  # the gap between a reply and the worker's next listen
CLAIM_TTL_SECONDS = 600  # a claimed job counts as work in progress for this long
JOB_ID = re.compile(r"^[a-f0-9]{16}$")


def worker_dir() -> Path:
    return Path(os.environ.get("BLINDSPOT_WORKER_DIR") or PROJECT_ROOT / "data" / "worker")


def _folder(name: str) -> Path:
    path = worker_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _age(path: Path) -> float:
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return float("inf")


def _touch(name: str) -> None:
    worker_dir().mkdir(parents=True, exist_ok=True)
    (worker_dir() / name).write_text(str(time.time()), encoding="utf-8")


def beat() -> None:
    _touch("listener.beat")


def stop_beat() -> None:
    try:
        (worker_dir() / "listener.beat").unlink()
    except OSError:
        pass


def worker_available() -> bool:
    """True if a worker is blocked in listen, is working on a claimed job, or just replied."""
    if _age(worker_dir() / "listener.beat") < BEAT_FRESH_SECONDS:
        return True
    if any(_age(path) < CLAIM_TTL_SECONDS for path in _folder("claimed").glob("*.json")):
        return True
    return _age(worker_dir() / "activity") < ACTIVITY_GRACE_SECONDS


def pending_jobs() -> int:
    return len(list(_folder("inbox").glob("*.json")))


def submit_job(prompt: str, allowed_ids: list[str]) -> str:
    job_id = uuid.uuid4().hex[:16]
    job = {"id": job_id, "created": time.time(), "prompt": prompt, "allowed_ids": sorted(allowed_ids)}
    _atomic_write(_folder("inbox") / f"{job_id}.json", json.dumps(job))
    return job_id


def claim_next_job() -> dict | None:
    """Take the oldest waiting job. The move is atomic, so two workers never claim the same one."""
    for path in sorted(_folder("inbox").glob("*.json"), key=lambda p: p.stat().st_mtime):
        target = _folder("claimed") / path.name
        try:
            os.replace(path, target)
        except OSError:
            continue  # another worker took it first
        _touch("activity")
        return json.loads(target.read_text(encoding="utf-8"))
    return None


def submit_reply(job_id: str, text: str) -> list[str]:
    """Validate a worker's reply and hand it to the app. Returns the reasons it was rejected, or [].

    A rejected reply leaves the job claimed so the worker can fix it and reply again.
    """
    if not JOB_ID.match(job_id):
        return [f"invalid job id: {job_id!r}"]
    claimed = _folder("claimed") / f"{job_id}.json"
    if not claimed.is_file():
        return [f"job {job_id} is not waiting for a reply (it may have expired or was already answered)"]
    job = json.loads(claimed.read_text(encoding="utf-8"))
    try:
        explanation = parse_explanation(text, mode=MODE)
    except ValueError as exc:
        return [f"reply is not a valid explanation: {exc}"]
    errors = validate_explanation(explanation, set(job["allowed_ids"]))
    if errors:
        return errors
    _atomic_write(_folder("outbox") / f"{job_id}.json", json.dumps({"text": text}))
    claimed.unlink()
    _touch("activity")
    return []


def cancel_job(job_id: str) -> None:
    for folder in ("inbox", "claimed", "outbox"):
        try:
            (_folder(folder) / f"{job_id}.json").unlink()
        except OSError:
            pass


def wait_for_reply(job_id: str, timeout: float, poll: float = 0.25) -> str | None:
    outbox = _folder("outbox") / f"{job_id}.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if outbox.is_file():
            text = json.loads(outbox.read_text(encoding="utf-8"))["text"]
            outbox.unlink()
            return text
        time.sleep(poll)
    return None


class ClaudeCodeWorkerClient:
    """ModelClient whose model is a Claude Code session listening on the job queue."""

    mode = MODE

    def __init__(self, timeout: float = 180.0):
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        if not worker_available():
            raise ModelError("no Claude Code worker is listening (start one with: python scripts/worker.py listen)")
        job_id = submit_job(prompt, evidence_ids_in_prompt(prompt))
        text = wait_for_reply(job_id, self.timeout)
        if text is None:
            cancel_job(job_id)
            raise ModelError(f"the Claude Code worker did not reply within {int(self.timeout)}s")
        return text
