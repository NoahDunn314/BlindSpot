# Snowflake CoCo integration guide

This guide is for Snowflake CoCo and for any human contributor who will improve Blindspot later. Start with [COCO_HANDOFF.md](COCO_HANDOFF.md) for the first task and its acceptance criteria.

**CoCo is a development agent for this repository. It is not the runtime agent.** Blindspot's investigation agent is the `InvestigationAgent` class, which runs inside the app and calls whichever model is configured. CoCo changes the code. It does not run inside the app.

The design goal is that you can improve one module at a time without rewriting the rest. Each module has a narrow interface, and each one can be tested alone.

## 1. Current architecture

```
fixtures/demo/*.json ──► FixtureAdapter ──► ingest() ──► SourceRecord[]
                                                             │
                 ┌───────────────────────────────────────────┤
                 ▼                                           ▼
     Demo mode: build_graph()                  Snowflake mode: SOURCE_RECORDS
     (resolution.py, discovery.py)             → 02_views.sql (resolution, edges)
                 │                             → 03_refresh.sql → ENTITIES,
                 │                               RELATIONSHIPS, REJECTED_MENTIONS
                 ▼                                           │
              Graph  ◄──────────────────────────────────────┘
                 │   (same type in both modes)
                 ▼
     RelationshipDiscovery.find(target)  →  Finding[] (ranked, scored, rule-based confidence)
                 │
                 ▼
     EvidenceRetriever  →  Evidence[]        (record IDs → summaries, URLs, dates)
                 │
                 ▼
     InvestigationAgent.explain()  →  Explanation  (model or deterministic template)
                 │
                 ▼
     FindingValidator  →  accept, or discard and fall back to deterministic
                 │
                 ▼
     app.py (Streamlit) + graph_view.py (pyvis)
```

Key properties:

- **Discovery decides what is connected.** The model never does.
- **Both modes produce the same `Graph` type.** The investigation workflow does not know which mode is active.
- **Snowflake does the joins and resolution.** Python does the short path search and the ranking. Both are deterministic.
- **Errors are reported, not hidden.** Snowflake failures raise `SnowflakeError`, which the UI shows. There is no silent fallback to demo data.

## 2. Snowflake schema and SQL setup

Files in `snowflake/sql/`, run in this order (the loader does this for you):

| File | Purpose |
|---|---|
| `01_schema.sql` | Database, schema, and tables. Idempotent (`IF NOT EXISTS`). |
| `02_views.sql` | `NORMALIZE_NAME` UDF and the views for mentions, resolution, edges, and rejections. |
| `03_refresh.sql` | Upserts the staged records, appends history, and rebuilds the derived tables. |
| `04_checks.sql` | Integrity checks. Every violation count should be 0. |

### Tables

| Table | Role | Key columns |
|---|---|---|
| `SOURCES` | One row per source, with access and licensing notes | `SOURCE_ID` |
| `SOURCE_RECORDS` | Current normalized record for each observation | `RECORD_ID`, `PAYLOAD` (VARIANT), `OBSERVED_AT`, `SOURCE_URL` |
| `SOURCE_RECORD_HISTORY` | Append-only history, one row per distinct payload | `RECORD_ID`, `PAYLOAD_SHA256` |
| `STG_SOURCE_RECORDS` | Loader staging, truncated each run | |
| `ENTITIES` | Resolved reference entities | `ENTITY_ID`, `REGISTRY_ID`, `STATE_CODE`, `NORMALIZED_NAME` |
| `RELATIONSHIPS` | Documented edges between resolved entities | `RELATIONSHIP_ID` (= source record ID), `MATCH_METHOD`, `STATUS` |
| `REJECTED_MENTIONS` | Mentions that could not be linked to exactly one entity | `RECORD_ID`, `REASON` |
| `INVESTIGATIONS` | Saved investigation runs | `INVESTIGATION_ID`, `NOTES` |
| `FINDINGS` | Saved findings with evidence IDs and explanation | `(INVESTIGATION_ID, FINDING_ID)` |

Snowflake does not enforce primary keys. They document intent and help BI tools.

### Views (resolution in SQL)

- `V_ENTITIES`: reference entities, deduplicated by `ENTITY_ID`.
- `V_MENTIONS`: one row per subject or object in each relationship record. Uses `LATERAL FLATTEN` over an `OBJECT_CONSTRUCT` of the subject and object.
- `V_RESOLVED_MENTIONS`: applies the resolution rules. A mention with a registry ID matches only by registry ID. A mention with a state matches by name and state. A mention with neither matches by name only when unique. Two or more candidates means ambiguous and is rejected.
- `V_RELATIONSHIPS`: both sides resolved. The weaker match method wins.
- `V_REJECTED_MENTIONS`: unresolved mentions with a reason.

### Loading

```bash
pip install -r requirements-snowflake.txt
cp .env.example .env          # then fill in your values
python scripts/load_snowflake.py
python scripts/check_parity.py
```

`check_parity.py` is the most important check. It compares the Python graph with the Snowflake graph (entities, edges, rejections, and findings). If you change the Python resolver or the SQL views, run it again.

**Status:** the SQL has not been run against a live account in this repository. The static test `tests/test_snowflake_static.py` checks the suffix list only. Run `load_snowflake.py` and `check_parity.py` once against a real account before relying on Snowflake mode.

## 3. Data-source adapter contract

Defined in `blindspot/adapters.py`:

```python
@dataclass(frozen=True)
class SourceInfo:
    source_id: str
    name: str
    access: str
    credentials_required: bool
    cost: str
    licensing: str
    update_cadence: str
    matching_limitations: str

class DataSourceAdapter(Protocol):
    info: SourceInfo
    def fetch(self) -> Iterable[SourceRecord]: ...
```

Rules for a new adapter:

1. Fill in `SourceInfo` completely. These fields are shown in the UI and stored in `SOURCES`.
2. Yield `SourceRecord` objects with stable `record_id` values. Re-fetching the same data must produce the same IDs (idempotent ingestion).
3. Keep the original URL in `url` and the source timestamp in `observed_at`.
4. Set `is_synthetic=False` for real data. Do not set it to `False` for fixtures.
5. Use `record_type = "entity_reference"` for reference entities. Use `"relationship"` for records that connect two mentions.
6. Do not fetch from the network in `fetch()` during tests. Use a `FixtureAdapter` for tests.

`SourceRecord` shape:

```json
{
  "record_id": "SD-001",
  "record_type": "relationship",
  "observed_at": "2026-03-02T00:00:00Z",
  "url": "fixture://demo/supplier_disclosures#SD-001",
  "payload": {
    "relation": "supplies_to",
    "status": "documented",
    "subject": {"name": "Pinecrest Metal Works LLC", "state": "IN"},
    "object": {"name": "Harborline Fabrication Co", "registry_id": "HF-1001"},
    "detail": "Bracket assemblies"
  }
}
```

Relationship relations currently used: `supplies_to`, `uses_facility`, `notice_applies_to`, `co_mentioned`. Status values: `documented`, `co_occurrence`.

## 4. Entity resolution (`EntityResolver`, `resolution.py`)

Input: a mention `{name, registry_id?, state?}`. Output: `Resolution(entity_id, method, reason)`.

Rules, strongest first:

| Condition | Result |
|---|---|
| `registry_id` given | Match on registry ID only. Not found means rejected. |
| No registry ID, `state` given | Match on normalized name and state. Must be unique. |
| No registry ID, no state | Match on normalized name. Must be unique, and the method is weak (`name_only`). |
| Otherwise | Rejected with a reason (no match, or ambiguous). |

`normalize_name` lowercases, replaces non-alphanumerics with spaces, and removes one trailing legal suffix (`inc`, `llc`, `ltd`, `co`, `corp`, `corporation`, `company`, `limited`). The SQL `NORMALIZE_NAME` function must match this exactly. `test_snowflake_static.py` checks the suffix list.

## 5. Relationship discovery algorithm (`RelationshipDiscovery`, `discovery.py`)

Only edges with `status = documented` are used. Two patterns are supported. Both are short.

**Shared upstream.** For target T, find the suppliers S with a `supplies_to` edge into T. Find the facilities F that two or more distinct suppliers use (`uses_facility`). Each such facility yields one finding with one branch per supplier:

```
T ←supplies_to— Si —uses_facility→ F
```

**Event exposure.** The same supplier-to-facility branches, plus a `notice_applies_to` edge from an event E into F:

```
T ←supplies_to— S —uses_facility→ F ←notice_applies_to— E
```

Ranking. Findings are sorted by score (descending), then by finding ID.

```
score = 2 × distinct_sources
      + 1 × (edges matched by registry ID)
      − 2 × (edges matched by name only)
```

Confidence (a rule label, not a probability):

- **Low:** any name-only edge, or fewer than two distinct sources.
- **High:** every edge is registry-matched, and at least two sources support the finding.
- **Moderate:** everything else.

Changing these rules requires updating `score_edges`, `tests/test_discovery.py::test_score_rules_and_confidence_labels`, and the explanation text in `app.py`.

Known limitation: the patterns trace the target's **suppliers**. An investigation of a supplier, such as Pinecrest, finds nothing, because the patterns do not trace the target's customers. This is a deliberate MVP limit.

## 6. Investigation-agent interface

`InvestigationAgent.explain(finding, evidence, names) -> (Explanation, notes)`

- `finding`: a `Finding`. Its `edges`, `paths`, and `evidence_ids` are the only facts the explanation may use.
- `evidence`: `dict[record_id, Evidence]` for the finding's records.
- `names`: `dict[entity_id, name]`.
- Returns an `Explanation` and a list of notes. Notes are shown in the UI. They explain validation failures and fallbacks.

Two implementations exist:

1. **Live model.** `AnthropicClient` sends the prompt from `build_prompt()` to the Anthropic Messages API. The response must be one JSON object. `parse_explanation()` parses it, and `FindingValidator` checks it. If parsing or validation fails, the agent discards the output and uses the deterministic template.
2. **Deterministic template.** `deterministic_explanation()` builds the explanation from the finding's edges and evidence only. It is labeled `deterministic_demo` everywhere.

To add a provider, implement `complete(prompt: str) -> str` and raise `ModelError` on failure. Nothing else changes.

### Expected JSON from a model

```json
{
  "title": "string",
  "summary": "string",
  "confirmed_facts": [{"text": "string", "evidence_ids": ["SD-001"]}],
  "inferences": [{"text": "string", "evidence_ids": ["FR-101", "FR-102"]}],
  "unknowns": ["string"],
  "why_it_matters": "string",
  "next_steps": ["string"]
}
```

### Validation (`FindingValidator`, `evidence.py`)

The explanation is rejected if any of these hold:

- A confirmed fact or inference has no evidence ID.
- An evidence ID is not among the finding's own records.
- `confirmed_facts`, `unknowns`, or `next_steps` is empty.
- Any text contains a percentage, the word "probability", or "percent".

## 7. Evidence retrieval (`EvidenceRetriever`, `evidence.py`)

Maps record IDs to `Evidence(record_id, source_id, source_name, url, observed_at, is_synthetic, summary)`. The summary is built from the record payload (`subject --relation--> object. detail`). Records that are not found are skipped, and the validator reports any citation to them.

## 8. Expected inputs and outputs

| Function | Input | Output |
|---|---|---|
| `ingest(adapters)` | Adapters | `list[SourceRecord]`, sorted by ID |
| `build_graph(records)` | Records | `Graph(entities, edges, rejections)` |
| `RelationshipDiscovery(graph).find(target_id)` | Entity ID | `list[Finding]` |
| `investigate(repo, target_id, agent)` | Repository, entity ID, agent | `InvestigationResult` |
| `Repository.load_graph()` | — | `Graph` |
| `Repository.load_records(ids)` | Record IDs | `list[SourceRecord]` |

## 9. Known limitations

- The demo data is small (18 records) and synthetic. Results show the mechanism, not real-world accuracy.
- No live source adapters are implemented. Only fixtures are loaded.
- Entity resolution uses exact normalized names. It does not handle typos, abbreviations, or translations.
- Patterns are fixed. Discovery cannot find a pattern that is not coded.
- The investigation trace only follows suppliers of the target. It does not follow customers, owners, or the target's own facilities.
- Only one trailing legal suffix is removed in name normalization.
- The rationale text stored in Snowflake differs from the Python version. Parity checks compare outcomes, not text.
- The Snowflake SQL is untested against a live account in this repository.
- Confidence labels are rules. They are not calibrated probabilities and must not be presented as such.

## 10. How to run tests and validate a proposed change

```bash
pip install -r requirements.txt
python -m pytest                    # unit, discovery, validation, UI smoke, static SQL checks
python evaluation/run_eval.py       # 17 deterministic checks, split by data layer and explanation layer
```

With a Snowflake account:

```bash
python scripts/load_snowflake.py    # must finish with all checks at 0
python scripts/check_parity.py      # must print PARITY OK
```

Before submitting a change, confirm all of these:

1. `pytest` passes.
2. `run_eval.py` reports 17/17, or the change adds checks with a reason.
3. If you changed resolution or discovery, `check_parity.py` passes on a Snowflake account.
4. If you added an adapter, its `SourceInfo` is complete and `tests` covers its record shape.
5. No credentials appear in code, SQL, or docs. `.env` is not committed.

## 11. Highest-value contributions for CoCo

Ordered by expected value:

1. **Verify the Snowflake path.** Run the loader and parity check against a real account, fix any SQL dialect issues, and add the result to the README.
2. **Add a multi-hop SQL candidate query.** Move the shared-upstream join into a SQL view (`V_CANDIDATE_SHARED_FACILITY`) and have discovery read it. Keep the Python scoring. Use `check_parity.py` to confirm the results match.
3. **Add a customer-direction pattern.** Let an investigation of a supplier find its customers' shared links. This needs new edge types and tests.
4. **Improve name resolution.** Add conservative normalization, such as punctuation and common abbreviations. Each rule needs a test that shows it does not merge two different companies.
5. **Add a real public source.** Document its access, licensing, cadence, and matching limits in `docs/DATA_SOURCES.md`. Implement an adapter, then add evaluation cases that use its records.
6. **Expand the evaluation set.** Add cases for ambiguous matches with different states, notices with no facility, and facilities shared by three or more suppliers.
7. **Improve the explanation prompt.** Measure its rejection rate with the validator. A lower rejection rate is only good if no invented claims pass.

Do not add a vector database, graph database, or agent framework for these tasks. The current structure is intended to stay small.
