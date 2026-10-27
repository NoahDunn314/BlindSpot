# Demo walkthrough

This walkthrough runs in demo mode with no credentials. Every record is synthetic.

## Start the app

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app opens with a red-bordered banner: **DEMO MODE**. Everything shown is a fixture.

## 1. Investigate Harborline Fabrication Co (vendor check)

Click **Harborline Fabrication Co** under "Or start a sample investigation". The sample is a fabricator that buys from three suppliers.

Expected result: **2 findings**, both rated **Moderate**.

### Finding A: Regulatory notice reaches Harborline through a shared facility (score 9)

Title: *Regional notice REN-2026-0412 (production hold) reaches Harborline Fabrication Co through Ironvale Castings Plant 2*

Evidence chain (three edges per branch, two branches):

| Step | Record | What the record documents |
|---|---|---|
| Supplier | `SD-001` (supplier disclosures) | Pinecrest Metal Works LLC supplies to Harborline. Matched by name and state. |
| Supplier | `SD-002` (supplier disclosures) | Bluefield Precision Parts Inc supplies to Harborline. Matched by name and state. |
| Facility | `FR-101` (facility registry) | Pinecrest uses Ironvale Castings Plant 2 for casting. Matched by registry ID. |
| Facility | `FR-102` (facility registry) | Bluefield uses Ironvale Castings Plant 2 for heat treatment. Matched by registry ID. |
| Event | `RN-0412` (regulatory notices) | A production hold applies to Ironvale Castings Plant 2. Matched by registry ID. |

What Blindspot says: the notice applies to a facility that two of Harborline's suppliers use. The notice may therefore reach Harborline's supply chain. This is an inference from documents, and the explanation labels it that way.

What Blindspot does not say: it does not know whether the hold affects parts Harborline buys, or whether the hold is still in effect.

Confidence is **Moderate** because the supplier edges rely on name and state rather than a registry ID.

### Finding B: Two suppliers share an upstream facility (score 6)

Title: *Bluefield Precision Parts Inc and Pinecrest Metal Works LLC both use Ironvale Castings Plant 2*

Evidence: `SD-001`, `SD-002`, `FR-101`, `FR-102`.

Neither supplier's own disclosure shows the shared dependency. It appears only when the two records are read together. This is the core "hidden connection" pattern.

### What was deliberately excluded

Open the expander **Mentions not linked and co-mentions not used**:

- **`RN-0455` is not linked.** The notice names "Pinecrest Metal Works LLC" with no state or registry ID. Two entities share that name, so the match is ambiguous and is rejected. This is an ambiguous-match example.
- **`NW-0077` is not used.** A trade article lists Harborline and Bluefield as exhibitors at the same expo. A co-mention is not a business relationship, so the graph shows it as a grey dotted "not used" edge.

### Explore the graph

- Solid lines are registry-ID matches.
- Dashed lines are name-and-state matches.
- Grey dotted lines are co-mentions, shown for context only.

Click a finding in the list to switch the graph and the detail panel.

## 2. Investigate Cedar Valley Packaging Co (control)

Click **Cedar Valley Packaging Co**. It also supplies Harborline, but it has no shared facility with any other supplier.

Expected result: **0 findings**, and the note: *"This is not evidence that no relationship exists. It means these sources did not show one."*

This shows that Blindspot does not invent a connection just because a company is connected to the same buyer.

## 3. Check the evidence and the validation layer

Open **Evidence and matching** in either finding to see:

- the source URL and observed date for each record (all `fixture://` URIs);
- the match method and rationale for each edge;
- the score breakdown and the confidence rules.

## 4. Run the evaluation

```bash
python evaluation/run_eval.py
```

Expected output ends with `17/17 checks passed.` The checks cover expected discovery, co-mention exclusion, ambiguous rejection, reproducibility, invented-citation rejection, numeric-risk rejection, and model-outage fallback.

## Optional: live model explanation

Add `ANTHROPIC_API_KEY` to `.env`. The finding explanations then come from the model, and each claim is validated against the finding's evidence. If validation fails, the app shows the deterministic explanation and a note explaining why.

The explanation caption shows which path was used.

## Optional: Snowflake mode

See the Snowflake section in [README.md](../README.md). The demo flow is the same, and the investigation can be saved to the `INVESTIGATIONS` and `FINDINGS` tables.
