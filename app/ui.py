"""Shared helpers for the Streamlit dashboard: state, styling and small components."""

from __future__ import annotations

import html
import sqlite3
from typing import Any

import streamlit as st

from atlas.config import AppConfig, load_config
from atlas.db.core import open_db
from atlas.db.settings import effective_config, load_profile
from atlas.models.enums import (
    BUSINESS_TYPE_LABELS,
    EMAIL_STATUS_LABELS,
    INDUSTRY_LABELS,
    INTERNSHIP_LABELS,
    OUTREACH_LABELS,
    SIZE_LABELS,
)
from atlas.providers import registry

CSS = """
<style>
:root { --atlas-border: #E2E8F0; --atlas-muted: #64748B; --atlas-ink: #0F172A; --atlas-accent: #1D4ED8; }
.block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1400px; }
h1, h2, h3 { letter-spacing: -0.01em; }
.atlas-title { font-size: 1.65rem; font-weight: 700; color: var(--atlas-ink); margin: 0 0 .15rem 0; }
.atlas-sub { color: var(--atlas-muted); margin: 0 0 1.1rem 0; font-size: .95rem; }
.atlas-card { background: #fff; border: 1px solid var(--atlas-border); border-radius: 12px; padding: 14px 16px; }
.atlas-kpi-label { color: var(--atlas-muted); font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; }
.atlas-kpi-value { font-size: 1.7rem; font-weight: 700; color: var(--atlas-ink); line-height: 1.2; }
.atlas-kpi-note { color: var(--atlas-muted); font-size: .78rem; }
.pill { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: .75rem; font-weight: 600;
        border: 1px solid transparent; margin-right: 4px; white-space: nowrap; }
.pill-green { background: #ECFDF5; color: #047857; border-color: #A7F3D0; }
.pill-amber { background: #FFFBEB; color: #B45309; border-color: #FDE68A; }
.pill-red { background: #FEF2F2; color: #B91C1C; border-color: #FECACA; }
.pill-blue { background: #EFF6FF; color: #1D4ED8; border-color: #BFDBFE; }
.pill-grey { background: #F1F5F9; color: #475569; border-color: #E2E8F0; }
.evidence { border-left: 3px solid #CBD5E1; padding: 4px 10px; margin: 4px 0 8px 0; color: #334155; font-size: .88rem;
            background: #F8FAFC; border-radius: 0 6px 6px 0; }
.evidence a { color: var(--atlas-accent); text-decoration: none; font-size: .78rem; }
.muted { color: var(--atlas-muted); font-size: .85rem; }
.factor-row { display: flex; align-items: center; gap: 10px; margin: 3px 0; font-size: .88rem; }
.factor-bar { flex: 1; background: #EEF2F7; border-radius: 6px; height: 8px; overflow: hidden; }
.factor-fill { background: var(--atlas-accent); height: 8px; }
div[data-testid="stMetricValue"] { font-size: 1.6rem; }
</style>
"""

STATUS_PILL = {
    "qualified": ("Qualified", "green"), "needs_review": ("Needs review", "amber"), "rejected": ("Rejected", "red"),
    None: ("Not assessed", "grey"),
}
INTERN_PILL = {
    "confirmed_opportunity": "green", "plausible_speculative": "blue", "eligibility_unclear": "amber",
    "explicitly_ineligible": "red",
}


def setup_page(title: str, subtitle: str = "") -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="atlas-title">{html.escape(title)}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="atlas-sub">{html.escape(subtitle)}</div>', unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def base_config() -> AppConfig:
    return load_config()


def conn() -> sqlite3.Connection:
    if "conn" not in st.session_state:
        cfg = base_config()
        c = open_db(cfg.paths.db_path, check_same_thread=False)
        registry.sync_to_db(c)
        st.session_state.conn = c
    return st.session_state.conn


def cfg() -> AppConfig:
    return effective_config(conn(), base_config())


def profile():
    return load_profile(conn())


def pill(text: str, color: str) -> str:
    return f'<span class="pill pill-{color}">{html.escape(text)}</span>'


def status_pill(status: str | None) -> str:
    label, color = STATUS_PILL.get(status, (status or "Unknown", "grey"))
    return pill(label, color)


def internship_pill(cat: str | None) -> str:
    if not cat:
        return pill("Internship: not assessed", "grey")
    return pill(INTERNSHIP_LABELS.get(cat, cat), INTERN_PILL.get(cat, "grey"))


def kpi(label: str, value: Any, note: str = "") -> str:
    return (f'<div class="atlas-card"><div class="atlas-kpi-label">{html.escape(label)}</div>'
            f'<div class="atlas-kpi-value">{html.escape(str(value))}</div>'
            f'<div class="atlas-kpi-note">{html.escape(note)}</div></div>')


def kpi_row(items: list[tuple[str, Any, str]]) -> None:
    cols = st.columns(len(items))
    for col, (label, value, note) in zip(cols, items):
        col.markdown(kpi(label, value, note), unsafe_allow_html=True)


def evidence_block(snippet: str, url: str | None, retrieved_at: str | None = None) -> str:
    link = ""
    if url and url.startswith("http"):
        when = f" · retrieved {retrieved_at[:10]}" if retrieved_at else ""
        link = f'<br><a href="{html.escape(url)}" target="_blank">{html.escape(url[:90])}</a>{when}'
    return f'<div class="evidence">“{html.escape(snippet[:400])}”{link}</div>'


def factor_bars(factors: list[dict]) -> str:
    rows = []
    for f in factors:
        w = f"{f['weight'] * 100:.0f}%"
        rows.append(
            f'<div class="factor-row"><div style="width:210px">{html.escape(f["label"])} <span class="muted">({w})</span></div>'
            f'<div class="factor-bar"><div class="factor-fill" style="width:{max(0, min(100, f["score"])):.0f}%"></div></div>'
            f'<div style="width:42px;text-align:right">{f["score"]:.0f}</div></div>'
            f'<div class="muted" style="margin:-2px 0 6px 220px">{html.escape(f["explanation"])}</div>')
    return "".join(rows)


def open_company(company_id: int) -> None:
    st.session_state["selected_company"] = int(company_id)
    st.switch_page("views/company.py")


def label_industry(v: str | None) -> str:
    return INDUSTRY_LABELS.get(v or "", v or "—")


def label_size(v: str | None) -> str:
    return SIZE_LABELS.get(v or "unknown", "Unknown")


def label_btype(v: str | None) -> str:
    return BUSINESS_TYPE_LABELS.get(v or "", v or "—")


def label_email(v: str | None) -> str:
    return EMAIL_STATUS_LABELS.get(v or "", v or "—")


def label_outreach(v: str | None) -> str:
    return OUTREACH_LABELS.get(v or "", v or "—")


def company_options(c: sqlite3.Connection, where: str = "") -> dict[int, str]:
    rows = c.execute(f"SELECT id, name, domain FROM companies WHERE merged_into_id IS NULL {where} ORDER BY name").fetchall()
    return {int(r["id"]): f"{r['name']}" + (f"  ·  {r['domain']}" if r["domain"] else "") for r in rows}


def empty_state(message: str) -> None:
    st.info(message, icon=":material/info:")
