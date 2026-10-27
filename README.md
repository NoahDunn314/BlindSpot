# Blindspot

**Discover what the data doesn't show alone.**

Blindspot answers one question: *what important connection would I miss if I examined these datasets separately?* It connects records across sources, traces short relationship paths, and shows the evidence for each connection. Every inference is labeled as one, and every finding lists what is still unknown.

It is built for procurement and vendor-risk professionals, but the architecture is general.

> **Status: hackathon MVP.** The demo mode works end to end. The Snowflake mode is implemented but not yet verified against a live account (see [Limitations](#limitations)). **All data in this repository is synthetic.**

## Quick start (demo mode, no credentials)

```bash
pip install -r requirements.txt
streamlit run app.py
```

Click **Harborline Fabrication Co** to see two evidence-backed findings. Click **Cedar Valley Packaging Co** to see a control case that correctly produces none. The full walkthrough is in [docs/DEMO_WALKTHROUGH.md](docs/DEMO_WALKTHROUGH.md).

Run the checks:

```bash
python -m pytest                  # unit, discovery, validation, UI smoke, SQL static checks
python evaluation/run_eval.py     # 17 deterministic checks: data layer and explanation layer
```

## What it does

1. **Ingests** normalized records from source adapters. Each record keeps its source ID, URL, and observation timestamp.
2. **Resolves** names to entities. A registry ID is the strongest match. A name plus state is medium. A name alone is weak. Ambiguous names are rejected, not guessed.
3. **Discovers** two short patterns through documented edges only:
   - *Shared upstream:* two suppliers of the target both use the same facility.
   - *Event exposure:* a notice applies to a facility that a supplier of the target uses.
4. **Ranks** findings with transparent rules, and labels confidence as Low, Moderate, or High. These labels are not probabilities.
5. **Explains** each finding. The explanation is either a deterministic template (labeled as a demo) or a live model response. A model response is accepted only if every claim cites the finding's own evidence and it contains no numeric risk estimates.
6. **Shows** an interactive graph, source evidence for each claim, the match rationale for each edge, the uncertainties, and suggested next steps.

Co-mentions (two companies named in one article) are shown but never used as evidence.

## Modes

| | Demo mode (default) | Snowflake mode |
|---|---|---|
| Set with | `BLINDSPOT_MODE=demo` | `BLINDSPOT_MODE=snowflake` |
| Credentials | None | Snowflake account and user, set in `.env` |
| Data | Fixtures in `fixtures/demo/` | `SOURCE_RECORDS`, resolved by SQL views |
| Discovery | Python (`discovery.py`) | Same Python code, over Snowflake-derived edges |
| On failure | Not applicable | Shows the error. Never falls back to demo data. |
| Banner | "DEMO MODE: all records are synthetic" | "SNOWFLAKE MODE: account …" |

## Snowflake mode

1. Copy the environment template and fill in your values. Set `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, and
   `SNOWFLAKE_WAREHOUSE`, plus either `SNOWFLAKE_PASSWORD` or `SNOWFLAKE_AUTHENTICATOR=externalbrowser`.
   Use a role with permission to create a database and schema for the initial load. Never commit `.env`.

   ```bash
   cp .env.example .env
   ```

2. Install the optional connector and load the schema and synthetic demo data. The loader provisions Snowflake
   regardless of `BLINDSPOT_MODE`, so the template can remain in demo mode until the load succeeds:

   ```bash
   pip install -r requirements-snowflake.txt
   python scripts/load_snowflake.py
   python scripts/check_parity.py
   ```

3. After the load and parity check pass, set `BLINDSPOT_MODE=snowflake` in `.env` and run `streamlit run app.py`.

Snowflake's role in the design:

- **Storage:** normalized source records as `VARIANT`, with an append-only history table.
- **Resolution:** `02_views.sql` resolves mentions with SQL joins and a `NORMALIZE_NAME` UDF.
- **Edges and rejections:** derived by SQL and stored in `RELATIONSHIPS` and `REJECTED_MENTIONS`.
- **Checks:** `04_checks.sql` verifies referential integrity.
- **Results:** investigations and findings can be saved to `INVESTIGATIONS` and `FINDINGS`.

The full schema is in [docs/COCO_INTEGRATION.md](docs/COCO_INTEGRATION.md). The first task for Snowflake CoCo, with acceptance criteria, is in [docs/COCO_HANDOFF.md](docs/COCO_HANDOFF.md).

## AI explanations

Set `ANTHROPIC_API_KEY` in `.env` to use a live model. `BLINDSPOT_MODEL` defaults to `claude-sonnet-5-5`.

Without a key, the app shows a deterministic explanation and says so in the caption. That explanation is a template built from the finding's own records. It is not an AI investigation.

The model never decides which facts exist. Its output is parsed and validated before it is shown. If validation fails, the app discards the output and shows the deterministic explanation with a note.

## Data sources

All current data is **synthetic**. Five fixture sources stand in for supplier disclosures, a facility registry, regulatory notices, news, and an entity reference list. Each one is documented in [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md), including access, credentials, cost, licensing, cadence, and matching limits.

No live source is implemented. To add one, implement the `DataSourceAdapter` protocol (see [docs/COCO_INTEGRATION.md](docs/COCO_INTEGRATION.md)).

## Project structure

```
app.py                      Streamlit UI
blindspot/
  models.py                 Shared data types
  config.py                 Settings from environment variables (.env optional)
  adapters.py               DataSourceAdapter protocol, fixture adapter, ingest()
  resolution.py             EntityResolver and normalize_name
  discovery.py              build_graph(), RelationshipDiscovery, scoring
  evidence.py               EvidenceRetriever, FindingValidator
  investigation.py          InvestigationAgent, model client, investigate()
  repository.py             Repository protocol and DemoRepository
  snowflake_repo.py         SnowflakeRepository (optional dependency)
  graph_view.py             pyvis graph for one finding
fixtures/demo/              Synthetic sources and sources.json metadata
snowflake/sql/              Schema, views, refresh, and checks
scripts/
  load_snowflake.py         Create schema and load demo data
  check_parity.py           Compare demo and Snowflake results
evaluation/run_eval.py      Deterministic evaluation harness
tests/                      pytest suite
docs/                       Data sources, demo walkthrough, CoCo handoff and guide
```

## Limitations

What the demo data does and does not prove:

- **It proves the mechanism, not real-world accuracy.** The companies, facilities, and notices are invented. The demo shows that the pipeline finds a shared-facility connection, cites its evidence, and refuses to claim more than the records show.
- **A missing connection is not evidence of absence.** The control case returns no finding because these sources do not show one.
- **Name matching is strict.** Typos, abbreviations, and translations are not matched. Ambiguous names are rejected.
- **Only the target's suppliers are traced.** An investigation of a supplier finds nothing, because the patterns do not follow customers.
- **The Snowflake SQL is untested here.** No Snowflake account was available during development. `scripts/check_parity.py` is the test to run first.
- **Confidence labels are rules.** They are not probabilities of real-world risk.
- **The UI was checked with Streamlit's headless test harness, not in a browser.** Layout and responsive behavior have not been visually verified.

## License

Not yet chosen. Add a license file before publishing.
