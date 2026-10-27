"""Evidence retrieval and finding validation.

EvidenceRetriever turns record IDs into displayable evidence with source URLs.
FindingValidator checks an Explanation against the finding it describes. It is
the gate between model output and the UI. Anything it rejects is never shown as
a finding.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping

from blindspot.models import Evidence, Explanation, Finding, SourceRecord

# Probabilities and percentages would look like calibrated risk. Blindspot does not produce them.
FORBIDDEN_NUMERIC_PATTERNS = [
    re.compile(r"\d+(\.\d+)?\s*%"),
    re.compile(r"\bprobabilit", re.IGNORECASE),
    re.compile(r"\bpercent\b", re.IGNORECASE),
]


def summarize_record(record: SourceRecord) -> str:
    payload = record.payload
    if record.record_type == "relationship":
        subject = payload["subject"].get("name", "")
        obj = payload["object"].get("name", "")
        detail = payload.get("detail", "")
        base = f"{subject} --{payload['relation']}--> {obj}"
        return f"{base}. {detail}" if detail else base
    return f"Reference entry: {payload['name']} ({payload['kind']})"


class EvidenceRetriever:
    def __init__(self, records: Iterable[SourceRecord], source_names: Mapping[str, str]):
        self._records = {record.record_id: record for record in records}
        self._source_names = dict(source_names)

    def get(self, record_ids: Iterable[str]) -> dict[str, Evidence]:
        found: dict[str, Evidence] = {}
        for record_id in sorted(set(record_ids)):
            record = self._records.get(record_id)
            if record is None:
                continue  # The validator reports any cited ID that has no record.
            found[record_id] = Evidence(
                record_id=record.record_id,
                source_id=record.source_id,
                source_name=self._source_names.get(record.source_id, record.source_id),
                url=record.url,
                observed_at=record.observed_at,
                is_synthetic=record.is_synthetic,
                summary=summarize_record(record),
            )
        return found


def validate_explanation(explanation: Explanation, allowed_ids: set[str]) -> list[str]:
    """Return every reason this explanation must not be shown. An empty list means it passed.

    Kept separate from FindingValidator so the Claude Code worker CLI can reject a bad reply
    with the same rules, using only the evidence IDs recorded in the job.
    """
    errors: list[str] = []

    if not explanation.confirmed_facts:
        errors.append("no confirmed facts listed")
    if not explanation.unknowns:
        errors.append("no unknowns or limitations listed")
    if not explanation.next_steps:
        errors.append("no next steps listed")

    for label, claims in (("confirmed fact", explanation.confirmed_facts), ("inference", explanation.inferences)):
        for claim in claims:
            if not claim.evidence_ids:
                errors.append(f"{label} has no evidence: {claim.text[:60]}")
                continue
            outside = set(claim.evidence_ids) - allowed_ids
            if outside:
                errors.append(f"{label} cites evidence outside this finding: {sorted(outside)}")

    texts = [explanation.title, explanation.summary, explanation.why_it_matters]
    texts += [claim.text for claim in explanation.confirmed_facts + explanation.inferences]
    texts += list(explanation.unknowns) + list(explanation.next_steps)
    for text in texts:
        if any(pattern.search(text) for pattern in FORBIDDEN_NUMERIC_PATTERNS):
            errors.append(f"contains a numeric probability or percentage: {text[:60]}")
            break

    return errors


class FindingValidator:
    """Checks that an explanation cites only evidence that supports this finding."""

    def __init__(self, finding: Finding, evidence: Mapping[str, Evidence]):
        self.finding = finding
        self.allowed_ids = set(finding.evidence_ids) & set(evidence)

    def validate(self, explanation: Explanation) -> list[str]:
        return validate_explanation(explanation, self.allowed_ids)
