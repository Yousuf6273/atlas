from __future__ import annotations

import asyncio

import pandas as pd
import streamlit as st
import ui

from atlas.assess.dossier import load_dossier
from atlas.contacts.verify import DomainMailChecker
from atlas.db import contacts as cdb
from atlas.models.enums import EMAIL_STATUS_LABELS, ROLE_LABELS
from atlas.pipeline.assess_company import verify_contacts
from atlas.pipeline.context import PipelineContext

ui.setup_page("Contact Research", "Professional contact channels published by each company, with their sources and "
                                  "verification status. ATLAS never guesses email addresses.")
c = ui.conn()

tab_one, tab_all, tab_missing, tab_rules = st.tabs(["One company", "All contacts", "Missing contacts", "How verification works"])

with tab_one:
    opts = ui.company_options(c, "AND qualification_status IS NOT NULL")
    if not opts:
        ui.empty_state("No assessed companies yet.")
    else:
        sel = st.session_state.get("contacts_company")
        ids = list(opts)
        cid = st.selectbox("Company", ids, index=ids.index(sel) if sel in ids else 0, format_func=lambda i: opts[i])
        rows = cdb.company_contacts(c, cid)
        if not rows:
            st.warning("No contact channels found on the official website or in the source records. Options: phone the "
                       "main line, visit in person, ask a teacher or family contact for an introduction, or check the "
                       "company's LinkedIn page yourself.")
        for r in rows:
            with st.container(border=True):
                head = f"**{r['channel'].replace('_', ' ').title()}** · `{r['value'][:100]}`"
                if r.get("name"):
                    head += f" · {r['name']}"
                if r.get("role"):
                    head += f" ({r['role']})"
                st.markdown(head)
                st.caption(f"Role category: {ROLE_LABELS.get(r['role_category'], r['role_category'])} · "
                           f"{'role mailbox' if r['is_generic'] else 'individual'} · source type: {r['source_type']} · "
                           f"quality {r['quality_score']:.0f}/100")
                if r["channel"] == "email":
                    st.markdown(ui.pill(EMAIL_STATUS_LABELS.get(r["verification_status"], r["verification_status"]),
                                        "green" if r["verification_status"] == "publicly_published" else
                                        "red" if r["verification_status"] == "invalid" else "amber"),
                                unsafe_allow_html=True)
                    for ch in r["checks"]:
                        mark = "✓" if ch["passed"] == 1 else ("✗" if ch["passed"] == 0 else "?")
                        st.caption(f"{mark} {ch['check_name']}: {ch['detail']}")
                for ev in r["evidence"][:2]:
                    st.markdown(ui.evidence_block(ev["snippet"] or "(link on page)", ev["source_url"], ev["retrieved_at"]),
                                unsafe_allow_html=True)
        if rows and st.button("Re-run verification checks (DNS)"):
            async def _rv():
                ctx = PipelineContext(cfg=ui.cfg(), conn=c, fetcher=None,  # type: ignore[arg-type]
                                      mail_checker=DomainMailChecker(ui.cfg().verification.dns_timeout_s),
                                      profile=ui.profile())
                await verify_contacts(ctx, load_dossier(c, cid))
            asyncio.run(_rv())
            st.rerun()

with tab_all:
    df = pd.read_sql_query(
        "SELECT co.name AS company, ct.channel, ct.value, ct.name AS person, ct.role, ct.role_category, "
        "ct.verification_status, ct.quality_score, ct.source_type, ct.source_url, ct.retrieved_at "
        "FROM contacts ct JOIN companies co ON co.id = ct.company_id WHERE co.merged_into_id IS NULL AND ct.is_current = 1 "
        "ORDER BY co.name, ct.quality_score DESC", c)
    if df.empty:
        ui.empty_state("No contacts yet.")
    else:
        ch = st.multiselect("Channels", sorted(df["channel"].unique()), default=[x for x in ("email", "contact_form",
                                                                                            "careers_page") if x in set(df["channel"])])
        view = df[df["channel"].isin(ch)] if ch else df
        view = view.assign(verification=view["verification_status"].map(lambda v: EMAIL_STATUS_LABELS.get(v, v)))
        st.dataframe(view.drop(columns=["verification_status"]), hide_index=True, use_container_width=True,
                     column_config={"source_url": st.column_config.LinkColumn("source"),
                                    "quality_score": st.column_config.ProgressColumn("quality", min_value=0, max_value=100,
                                                                                     format="%.0f")})

with tab_missing:
    miss = pd.read_sql_query(
        "SELECT co.id, co.name, co.website, co.qualification_status FROM companies co WHERE co.merged_into_id IS NULL "
        "AND co.qualification_status IN ('qualified','needs_review') AND NOT EXISTS (SELECT 1 FROM contacts ct WHERE "
        "ct.company_id = co.id AND ct.is_current = 1 AND ct.quality_score > 0 AND ct.channel IN "
        "('email','contact_form','careers_page')) ORDER BY co.name", c)
    st.caption("Relevant companies without a usable channel are kept — they are not dropped from your list.")
    st.dataframe(miss, hide_index=True, use_container_width=True, column_config={"website": st.column_config.LinkColumn()})
    st.markdown("Legitimate next steps: check the website's contact page manually (some use JavaScript forms ATLAS "
                "cannot read), call the published phone number, use the company's LinkedIn page yourself, or ask for an "
                "introduction through school or family contacts.")

with tab_rules:
    st.markdown(
        "- **Syntax** – valid address format (RFC 5322).\n"
        "- **Domain / MX** – DNS shows whether the domain accepts mail (MX record, or an address record as implicit MX).\n"
        "- **Source** – the address was published on the cited official page (visible text, mailto link or structured data).\n"
        "- **Consistency** – address is on the company's own website domain (free webmail is flagged).\n"
        "- **Individual vs role mailbox** – personal inboxes are labelled *inbox not verified*.\n\n"
        "**What is never done:** SMTP probing, guessing addresses from name patterns, or scraping private profiles. "
        "A DNS check proves the domain can receive mail — never that a particular mailbox exists.")
