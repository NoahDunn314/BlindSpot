# Data sources

Blindspot has two kinds of data: the **synthetic demo fixtures** that ship with the repository, and **live sources** that are documented here but not yet implemented.

**Every record in the current repository is synthetic.** The companies, facilities, notices, and articles are fictional. They are labeled `SYNTHETIC` in the UI and in the Snowflake `IS_SYNTHETIC` column. Nothing in this repository is a real-world finding.

## Synthetic demo sources (implemented)

Each source is a JSON file in `fixtures/demo/`, described in `fixtures/demo/sources.json`, and loaded by a `FixtureAdapter` in `blindspot/adapters.py`.

| Source ID | Stands in for | Access | Credentials | Cost | Update cadence | Relevant fields | Matching limitations |
|---|---|---|---|---|---|---|---|
| `entity_reference` | A reference table built from public company and facility registries | Fixture file | No | Free | Static fixture | `entity_id`, `kind`, `name`, `registry_id`, `state` | Assumes the list is complete |
| `supplier_disclosures` | Buyer-published supplier lists | Fixture file | No | Free | Static fixture | `subject` and `object` (name, state, registry ID), `relation` | Often no registry ID, so matches use name and state |
| `facility_registry` | Operator-to-facility registry | Fixture file | No | Free | Static fixture | `subject` (operator), `object` (facility), `relation` | Links can be incomplete. A missing link proves nothing. |
| `regulatory_notices` | Government notices that name a facility or company | Fixture file | No | Free | Static fixture | `subject` (notice), `object` (facility or company) | Notices often name companies without a state or ID. These are rejected as ambiguous. |
| `trade_news` | News articles | Fixture file | No | Free | Static fixture | `subject`, `object`, `status = co_occurrence` | A co-mention is not a relationship. Blindspot never uses co-mentions for discovery. |

Licensing: all fixtures are fictional and carry no license. Real news content is copyrighted and must not be stored without a license. Real supplier lists and registries may restrict reuse. Check each publisher's terms before using live data.

## Live sources (documented candidates, not implemented)

Before a live source is added, document these items in `sources.json` (or an equivalent file): how to access it, whether credentials are needed, whether it is free, its licensing terms, its update cadence, its relevant fields, and its matching limitations. Then implement a `DataSourceAdapter` for it. The adapter contract is described in [COCO_INTEGRATION.md](COCO_INTEGRATION.md).

Candidate categories from the product brief:

- **Company and corporate disclosures.** Look for a public filing source with a documented access policy and a stable company identifier.
- **Government notices and regulatory announcements.** Look for an official feed with a documented update cadence.
- **Public facility or business relationship records.** Look for a public registry that links operators to facilities.
- **Snowflake Marketplace listings.** Use only when the listing's license permits the intended use.

No live source has been tested against its terms of service in this repository. Verify each one before depending on it.
