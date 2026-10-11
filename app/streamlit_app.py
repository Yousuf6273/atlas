"""ATLAS dashboard entry point: `streamlit run app/streamlit_app.py`."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

st.set_page_config(page_title="ATLAS · Internship Intelligence", page_icon=":material/travel_explore:", layout="wide")

import ui  # noqa: E402

from atlas.logging_setup import setup_logging  # noqa: E402
from atlas.pipeline.runner import recover_stale_jobs  # noqa: E402

cfg = ui.base_config()
setup_logging(cfg.paths.log_dir)
if "recovered" not in st.session_state:
    st.session_state.recovered = recover_stale_jobs(cfg)

pages = {
    "Intelligence": [
        st.Page("views/overview.py", title="Overview", icon=":material/dashboard:", default=True),
        st.Page("views/search.py", title="Search", icon=":material/travel_explore:"),
        st.Page("views/explorer.py", title="Prospect Explorer", icon=":material/table_view:"),
        st.Page("views/company.py", title="Company Profile", icon=":material/apartment:"),
        st.Page("views/contacts.py", title="Contact Research", icon=":material/contact_mail:"),
    ],
    "Outreach": [
        st.Page("views/outreach.py", title="Outreach Studio", icon=":material/edit_note:"),
        st.Page("views/crm.py", title="Pipeline (CRM)", icon=":material/view_kanban:"),
    ],
    "Insights": [
        st.Page("views/analytics.py", title="Analytics", icon=":material/monitoring:"),
        st.Page("views/review.py", title="Review & Evaluation", icon=":material/fact_check:"),
    ],
    "System": [
        st.Page("views/settings.py", title="Settings", icon=":material/settings:"),
    ],
}

with st.sidebar:
    st.markdown("### ATLAS")
    st.caption("Internship intelligence · free & local")

nav = st.navigation(pages)
nav.run()

with st.sidebar:
    st.divider()
    st.caption("Drafts are never sent automatically. Every message must be reviewed and sent by you.")
