"""Blindspot: evidence-backed relationship discovery. Run with: streamlit run app.py"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from blindspot.config import Settings, load_dotenv, load_settings
from blindspot.graph_view import render_finding_graph
from blindspot.investigation import AnthropicClient, InvestigationAgent, investigate, search_entities
from blindspot.models import Finding, Graph, InvestigationResult
from blindspot.repository import open_repository
from blindspot.snowflake_repo import SnowflakeError

SAMPLES = [
    ("Harborline Fabrication Co", "ent-harborline", "Vendor check. Three suppliers, one of which shares a facility with another."),
    ("Cedar Valley Packaging Co", "ent-cedar", "Control. A supplier with no shared upstream link. Expect no finding."),
]
CONFIDENCE_COLORS = {"High": "green", "Moderate": "orange", "Low": "red"}


@st.cache_resource(show_spinner="Opening data source...")
def get_repository(settings: Settings):
    return open_repository(settings)


def make_agent(settings: Settings) -> InvestigationAgent:
    if settings.anthropic_api_key:
        return InvestigationAgent(AnthropicClient(settings.anthropic_api_key, settings.model))
    return InvestigationAgent()


def run_investigation(repository, settings: Settings, target_id: str) -> None:
    st.session_state.result = investigate(repository, target_id, make_agent(settings))
    st.session_state.selected_finding = None


def render_banner(repository, settings: Settings) -> None:
    if repository.mode == "demo":
        st.warning(
            "**DEMO MODE.** Every record is a SYNTHETIC fixture with fictional companies, facilities, and notices. "
            "Nothing shown here is a real-world finding."
        )
    else:
        st.info(f"**SNOWFLAKE MODE.** Reading from account `{settings.snowflake_account}`, database `{settings.snowflake_database}`.")
        if st.session_state.get("has_synthetic"):
            st.warning(
                "The loaded Snowflake data includes SYNTHETIC fixtures with fictional companies, facilities, and notices. "
                "Findings from those records are not real-world findings."
            )


def render_explanation_mode(result: InvestigationResult, settings: Settings) -> None:
    modes = {explanation.mode for explanation in result.explanations.values()}
    if not modes:
        return
    if modes == {"live_model"}:
        st.caption(f"Explanation: live AI-generated with model `{settings.model}`, validated against source evidence.")
    else:
        st.caption("Explanation: deterministic demo template. No model API key is configured, so this is not an AI-generated investigation.")


def render_finding_detail(result: InvestigationResult, graph: Graph, finding: Finding) -> None:
    explanation = result.explanations[finding.finding_id]
    st.markdown(f"#### {finding.title}")
    st.markdown(f"Confidence: **:{CONFIDENCE_COLORS[finding.confidence]}[{finding.confidence}]**  ·  Rule score: {finding.score}")
    st.write(explanation.summary)
    st.markdown(f"**Why it may matter.** {explanation.why_it_matters}")

    tabs = st.tabs(["Confirmed facts", "Inferences", "Unknowns", "Next steps", "Evidence and matching"])
    with tabs[0]:
        for claim in explanation.confirmed_facts:
            st.markdown(f"- {claim.text}  \n  `{', '.join(claim.evidence_ids)}`")
    with tabs[1]:
        st.caption("Reasoning from the records above. Not documented facts.")
        for claim in explanation.inferences:
            st.markdown(f"- {claim.text}  \n  `{', '.join(claim.evidence_ids)}`")
    with tabs[2]:
        for item in explanation.unknowns:
            st.markdown(f"- {item}")
    with tabs[3]:
        for index, step in enumerate(explanation.next_steps, start=1):
            st.markdown(f"{index}. {step}")
    with tabs[4]:
        evidence_rows = [
            {
                "Record": item.record_id,
                "Source": item.source_name,
                "Observed": item.observed_at[:10],
                "Synthetic": "yes" if item.is_synthetic else "no",
                "Summary": item.summary,
                "Source URL": item.url,
            }
            for record_id, item in sorted(result.evidence.items())
            if record_id in finding.evidence_ids
        ]
        st.dataframe(evidence_rows, hide_index=True)

        names = {entity.entity_id: entity.name for entity in graph.entities}
        edge_rows = [
            {
                "Record": edge.source_record_id,
                "Relation": edge.relation,
                "From": names.get(edge.from_entity_id, edge.from_entity_id),
                "To": names.get(edge.to_entity_id, edge.to_entity_id),
                "Match": edge.match_method,
                "Rationale": edge.match_rationale,
            }
            for edge in finding.edges
        ]
        st.dataframe(edge_rows, hide_index=True)
        with st.expander("How the rule score and confidence were computed"):
            st.json(finding.score_breakdown)
            st.markdown(
                "Confidence is **Low** if any edge is name-only or fewer than two sources support it. "
                "It is **High** if every edge is registry-matched and at least two sources support it. "
                "Otherwise it is **Moderate**. These are rule labels, not probabilities of real-world risk."
            )


def main() -> None:
    st.set_page_config(page_title="Blindspot", page_icon="🔭", layout="wide")
    load_dotenv()
    try:
        settings = load_settings()
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    st.title("Blindspot")
    st.write(
        "Blindspot connects records across public datasets to find relationships that are hard to see when each source is "
        "read alone. Every connection is shown with its source records, and every inference is labeled as one."
    )

    try:
        repository = get_repository(settings)
        if "graph" not in st.session_state:
            st.session_state.graph = repository.load_graph()
            if repository.mode == "snowflake":
                st.session_state.has_synthetic = repository.has_synthetic_records()
    except SnowflakeError as exc:
        st.error(f"Snowflake error. Blindspot did not fall back to demo data. Details: {exc}")
        st.stop()

    render_banner(repository, settings)
    graph = st.session_state.graph
    st.session_state.setdefault("result", None)
    st.session_state.setdefault("selected_finding", None)

    search_col, action_col = st.columns([3, 1])
    with search_col:
        query = st.text_input("Search a company, facility, or registry ID", placeholder="e.g. Harborline Fabrication")
    matches = search_entities(list(graph.entities), query) if query else []
    chosen = None
    if matches:
        chosen = st.selectbox("Matching entity", options=matches, format_func=lambda e: f"{e.name} ({e.kind}, {e.state or 'no state'})")
    elif query:
        st.caption("No entity matches that query in the loaded sources.")
    with action_col:
        st.write("")
        st.write("")
        investigate_clicked = st.button("Investigate", type="primary", disabled=chosen is None, use_container_width=True)

    st.caption("Or start a sample investigation:")
    sample_cols = st.columns(len(SAMPLES))
    for column, (label, target_id, description) in zip(sample_cols, SAMPLES):
        with column:
            if st.button(label, key=f"sample-{target_id}", use_container_width=True):
                run_investigation(repository, settings, target_id)
            st.caption(description)

    if investigate_clicked and chosen is not None:
        run_investigation(repository, settings, chosen.entity_id)

    result = st.session_state.result
    if result is None:
        st.info("Search for an entity or choose a sample to start an investigation.")
        return

    st.divider()
    st.subheader(f"Investigation: {result.target.name}")
    st.caption(f"{result.target.kind} · registry {result.target.registry_id or 'none'} · state {result.target.state or 'none'}")
    render_explanation_mode(result, settings)
    for note in result.notes:
        if "failed" in note or "could not" in note or "Live model call" in note:
            st.warning(note)
        else:
            st.info(note)

    co_mentions = [e for e in graph.edges if e.status == "co_occurrence" and result.target.entity_id in (e.from_entity_id, e.to_entity_id)]
    metric_cols = st.columns(4)
    metric_cols[0].metric("Findings", len(result.findings))
    metric_cols[1].metric("Source records cited", len(result.evidence))
    metric_cols[2].metric("Mentions not linked", len(result.rejections))
    metric_cols[3].metric("Co-mentions not used", len(co_mentions))

    if not result.findings:
        st.write("No finding was produced. Try another entity or check that the relevant sources are loaded.")
    else:
        list_col, graph_col = st.columns([1, 2])
        with list_col:
            st.markdown("**Findings**")
            # Radio options are indexes because Finding holds a dict and cannot be a widget option.
            index = st.radio(
                "Finding",
                options=list(range(len(result.findings))),
                format_func=lambda i: f"{result.findings[i].title}  ·  {result.findings[i].confidence}",
                label_visibility="collapsed",
                key=f"finding-{result.target.entity_id}",
            )
        selected = result.findings[index]
        with graph_col:
            st.markdown("**Relationship graph**")
            st.caption("Solid line: registry-ID match. Dashed line: name-and-state match. Grey dotted: co-mention, not used.")
            components.html(render_finding_graph(graph, selected), height=500)

        st.divider()
        render_finding_detail(result, graph, selected)

    with st.expander("Mentions not linked and co-mentions not used"):
        if result.rejections:
            st.dataframe(
                [{"Record": r.record_id, "Mention": r.mention_name, "Reason": r.reason} for r in result.rejections],
                hide_index=True,
            )
        for edge in co_mentions:
            st.markdown(f"- `{edge.source_record_id}`: {edge.source_url}. Article co-mention only. Not used for discovery.")

    if repository.mode == "snowflake" and result.findings:
        if st.button("Save this investigation to Snowflake"):
            try:
                investigation_id = repository.save_investigation(result)
                st.success(f"Saved to INVESTIGATIONS and FINDINGS. Investigation ID: {investigation_id}")
            except SnowflakeError as exc:
                st.error(f"Could not save the investigation. Details: {exc}")

    st.divider()
    st.caption(
        "Blindspot reports documented connections and rule-based confidence labels. It does not estimate the probability of real-world risk. "
        "A missing connection in these sources does not prove that none exists."
    )


main()
