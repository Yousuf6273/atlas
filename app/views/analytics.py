from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st
import ui

from atlas.db.queries import (
    claim_stats,
    overview_counts,
    prospect_rows,
    source_coverage,
)
from atlas.evaluation.metrics import compute_metrics
from atlas.models.enums import INDUSTRY_LABELS, INTERNSHIP_LABELS

ui.setup_page("Analytics", "Funnel, coverage and quality — computed from your data only. Rates are shown with their "
                           "sample sizes.")
c = ui.conn()
k = overview_counts(c)
if k["discovered"] == 0:
    ui.empty_state("No data yet.")
    st.stop()

ACCENT = "#1D4ED8"

st.markdown("#### Prospecting funnel")
funnel = pd.DataFrame([
    ("Discovered", k["discovered"]), ("Researched", k["researched"]), ("Qualified", k["qualified"]),
    ("Usable contact", k["usable_contact"]), ("Draft prepared", k["drafts"]), ("Contacted", k["contacted"]),
    ("Replied", k["replied"]), ("Opportunities", k["opportunities"])], columns=["Stage", "Companies"])
funnel["order"] = range(len(funnel))
bars = alt.Chart(funnel).mark_bar(color=ACCENT, cornerRadiusEnd=4).encode(
    x=alt.X("Companies:Q"), y=alt.Y("Stage:N", sort=alt.SortField("order"), title=None),
    tooltip=["Stage", "Companies"])
labels = bars.mark_text(align="left", dx=4, color="#0F172A").encode(text="Companies:Q")
st.altair_chart((bars + labels).properties(height=280), width="stretch")


def rate(n: int, d: int) -> str:
    return f"{n / d:.0%} ({n}/{d})" if d else "— (no data)"


cols = st.columns(3)
cols[0].metric("Contact availability (qualified)", rate(
    len([r for r in prospect_rows(c) if r["qualification_status"] == "qualified" and r["has_usable_contact"]]),
    k["qualified"]))
cols[1].metric("Response rate", rate(k["replied"], k["contacted"]))
cols[2].metric("Opportunity rate", rate(k["opportunities"], k["contacted"]))

rows = prospect_rows(c, include_unassessed=False)
left, right = st.columns(2, gap="large")
with left:
    st.markdown("#### Industry breakdown (assessed)")
    df = pd.DataFrame([{"Industry": INDUSTRY_LABELS.get(r["primary_industry"] or "", "Unclassified"),
                        "Status": (r["qualification_status"] or "").replace("_", " ")} for r in rows])
    if not df.empty:
        ch = alt.Chart(df).mark_bar().encode(
            y=alt.Y("Industry:N", sort="-x", title=None), x=alt.X("count():Q", title="Companies"),
            color=alt.Color("Status:N", scale=alt.Scale(domain=["qualified", "needs review", "rejected"],
                                                        range=["#047857", "#B45309", "#B91C1C"])))
        st.altair_chart(ch.properties(height=300), width="stretch")
with right:
    st.markdown("#### Internship suitability")
    df2 = pd.DataFrame([{"Category": INTERNSHIP_LABELS.get(r["internship_category"] or "", "Not assessed")} for r in rows
                        if r["qualification_status"] != "rejected"])
    if not df2.empty:
        ch2 = alt.Chart(df2).mark_bar(color=ACCENT).encode(y=alt.Y("Category:N", sort="-x", title=None),
                                                           x=alt.X("count():Q", title="Companies"))
        st.altair_chart(ch2.properties(height=300), width="stretch")

st.markdown("#### Source coverage and quality")
cov = pd.DataFrame(source_coverage(c))
if not cov.empty:
    cov["qualified rate"] = (cov["qualified"] / cov["companies"]).map(lambda x: f"{x:.0%}")
    cov["website rate"] = (cov["with_website"] / cov["companies"]).map(lambda x: f"{x:.0%}")
    st.dataframe(cov, hide_index=True, width="stretch")
cs = claim_stats(c)
if cs:
    st.markdown("#### Evidence collected")
    st.dataframe(pd.DataFrame([{"Claim type": k_, "Claims": v["count"], "Labelled inferences": v["inferences"]}
                               for k_, v in cs.items()]), hide_index=True, width="stretch")

st.markdown("#### Evaluation (from your manual reviews)")
metrics = compute_metrics(c)
if all(m.n == 0 for m in metrics):
    st.caption("No manual reviews yet — use Review & Evaluation to review your first 25–50 companies. ATLAS does not "
               "report accuracy figures it has not measured.")
else:
    st.dataframe(pd.DataFrame([{"Metric": m.label, "Result": m.fmt(), "Reviewed": m.n} for m in metrics]),
                 hide_index=True, width="stretch")
