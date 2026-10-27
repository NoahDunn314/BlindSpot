# CoCo handoff: verify and harden Blindspot's Snowflake path

Read this first, then `docs/COCO_INTEGRATION.md` for the full architecture, schema, and interface reference.

## Project goal

Blindspot answers: *what important connection would I miss if I examined these datasets separately?* It resolves entity names across source records, finds short documented relationship paths, and explains each finding with cited evidence. Every claim is labeled as a confirmed fact, an inference, or an unknown. The first audience is procurement and vendor-risk teams at small and midsize businesses.

Product rule: **Blindspot must never confuse a plausible story with a verified discovery.** Deterministic discovery decides what is connected. A model only explains findings that discovery produced, and its output is validated before display.

## Current state

- Demo mode works end to end over **synthetic** fixtures (fictional companies, 18 records, five sources). `python -m pytest` passes (36 tests, including fake-connection tests of `SnowflakeRepository`) and `python evaluation/run_eval.py` reports 17/17.
- Snowflake mode is **implemented but has never run against a Snowflake account.** The SQL is unverified. This is your first job.
- No live public-data adapters exist. All data is synthetic and labeled so.

## Your task

**Task 1 (required): make the Snowflake path work.**

1. Configure `.env` from `.env.example` (the user supplies credentials. Never commit `.env`) and set `BLINDSPOT_MODE=snowflake`.
2. Run `python scripts/load_snowflake.py`. Fix any SQL dialect or connector errors in `snowflake/sql/*.sql` and `blindspot/snowflake_repo.py`.
3. Run `python scripts/check_parity.py`. Fix differences until it prints `PARITY OK`.

**Acceptance criteria:**

- `load_snowflake.py` finishes with every check in `04_checks.sql` at 0.
- `check_parity.py` prints `PARITY OK` (entities, edges, rejections, and findings match demo mode).
- `python -m pytest` and `python evaluation/run_eval.py` still pass unchanged.
- The Streamlit app in Snowflake mode shows the same two Harborline findings as demo mode and can save an investigation to `INVESTIGATIONS` and `FINDINGS`.

**Task 2 (only after Task 1 passes): move the shared-facility join into SQL.**

Add a view such as `V_CANDIDATE_SHARED_FACILITY` that finds facilities used by two or more suppliers of the same buyer. Have the Snowflake repository return those candidates. Keep scoring and confidence in Python (`score_edges`). Confirm with `check_parity.py` that findings are unchanged.

## Code map

| Path | Role |
|---|---|
| `blindspot/models.py` | Shared dataclasses: `SourceRecord`, `Entity`, `Edge`, `Graph`, `Finding`, `Explanation` |
| `blindspot/adapters.py` | `DataSourceAdapter` protocol, `FixtureAdapter`, `ingest()` |
| `blindspot/resolution.py` | `EntityResolver`, `normalize_name` (reference implementation of resolution rules) |
| `blindspot/discovery.py` | `build_graph()`, `RelationshipDiscovery`, `score_edges()` |
| `blindspot/evidence.py` | `EvidenceRetriever`, `FindingValidator` |
| `blindspot/investigation.py` | `InvestigationAgent`, model client, `investigate()` |
| `blindspot/repository.py` | `Repository` protocol, `DemoRepository` |
| `blindspot/snowflake_repo.py` | `SnowflakeRepository` (what you will mostly edit) |
| `snowflake/sql/01_schema.sql` | Tables |
| `snowflake/sql/02_views.sql` | `NORMALIZE_NAME` UDF, resolution views (SQL mirror of `resolution.py`) |
| `snowflake/sql/03_refresh.sql` | MERGE, history append, rebuild of derived tables |
| `snowflake/sql/04_checks.sql` | Integrity checks |
| `scripts/load_snowflake.py`, `scripts/check_parity.py` | Loader and Python-vs-SQL parity check |
| `evaluation/run_eval.py`, `tests/` | Evaluation harness and pytest suite |

Data flow: fixtures, then `ingest()`, then `SOURCE_RECORDS` in Snowflake. SQL views resolve mentions into `ENTITIES`, `RELATIONSHIPS`, and `REJECTED_MENTIONS`. `SnowflakeRepository.load_graph()` reads those tables into the same `Graph` type that demo mode builds in Python. Discovery then runs in Python in both modes.

## Rules that must not change

These are product guarantees. If a change appears to require breaking one, stop and ask the user.

1. **Resolution rules.** Registry ID is the strongest match and is never overridden by name. A name with a state needs the state to match. A name alone must match exactly one entity. Two or more candidates is ambiguous and is rejected, never guessed. The Python resolver is the reference. The SQL must match it.
2. **Co-mentions are never evidence.** Edges with `status = co_occurrence` are excluded from discovery.
3. **No silent fallback.** Snowflake failures raise `SnowflakeError` and are shown. Never fall back to demo data.
4. **Scores and confidence are rule labels, not probabilities.** Do not present them as risk percentages.
5. **The validator stays strict.** `FindingValidator` rejects uncited claims, citations outside the finding, empty unknowns, and numeric probabilities.
6. **Synthetic data stays labeled.** Do not present fixtures as real-world findings.
7. **No credentials in code, SQL, or docs.** `.env` is gitignored.

## Known quirks to check first

- `NORMALIZE_NAME` is a SQL UDF using `REGEXP_REPLACE`. It must produce the same output as `normalize_name()` in Python, including removing exactly one trailing suffix. `tests/test_snowflake_static.py` checks only the suffix list.
- `V_MENTIONS` uses `LATERAL FLATTEN` over `OBJECT_CONSTRUCT('subject', ..., 'object', ...)`. Confirm key and value access works as written.
- `stage_sources` uses `executemany` with `INSERT ... SELECT`. This may need to become `VALUES`.
- `SnowflakeRepository.execute_script` uses `connection.execute_string`. In the installed connector it exists on the connection, not the cursor.
- `OBSERVED_AT` is `TIMESTAMP_NTZ`. The Python side expects ISO strings like `2026-03-02T00:00:00Z`. `_to_iso` converts. Check that timezone handling is consistent.
- Rationale text differs between modes by design. Parity compares outcomes, not text.

## Known limitations (not part of your task unless asked)

- Discovery traces only the target's suppliers. Investigating a supplier such as Pinecrest finds nothing.
- Name matching is exact on normalized names.
- Only two patterns exist: shared upstream and event exposure.
- No live source adapters.

## How to validate any change

```bash
python -m pytest
python evaluation/run_eval.py
python scripts/load_snowflake.py
python scripts/check_parity.py
```

## What to report back

- Which files you changed and why, as a short list.
- The final output of all four commands above.
- Anything you could not fix, with the exact Snowflake error.
- Any rule from "Rules that must not change" that you believe is wrong, as a question for the user. Do not change it yourself.

## What the existing tests do and do not cover

- `tests/test_snowflake_repo.py` runs `SnowflakeRepository` against a fake connection. It covers row mapping, timestamp formatting, parameter binding, error wrapping, and the save path. It **cannot** validate any SQL.
- `tests/test_snowflake_static.py` checks that the SQL suffix list matches Python and that the SQL files exist.
- Only `load_snowflake.py` and `check_parity.py` exercise the real SQL, and both need a live account.
