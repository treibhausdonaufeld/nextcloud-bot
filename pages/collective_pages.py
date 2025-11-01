from typing import List

import streamlit as st

from lib.menu import menu
from lib.nextcloud.models.collective_page import CollectivePage
from lib.settings import (
    _,
    settings,
)
from lib.streamlit_oauth import load_user_data

# Streamlit app starts here
title = _("Collective Pages").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="✅", layout="wide")

menu()
load_user_data()

st.title(title)


# load collective pages
@st.cache_data(ttl=3600)
def load_collective_pages() -> List[CollectivePage]:
    return CollectivePage.get_all(limit=1000)


pages = load_collective_pages()

# Search by filePath
search_fp = st.text_input(_("Search filePath"), placeholder="/group/subgroup/readme.md")
if search_fp:
    pages = [
        p
        for p in pages
        if p.ocs and search_fp.lower() in (p.ocs.filePath or "").lower()
    ]

# Build dataframe
df = {
    _("ID"): [p.id for p in pages],
    _("Title"): [p.title for p in pages],
    _("filePath"): [p.ocs.filePath if p.ocs else "" for p in pages],
    _("collectivePath"): [p.ocs.collectivePath if p.ocs else "" for p in pages],
    _("is_readme"): [p.is_readme for p in pages],
    _("URL"): [p.url or "" for p in pages],
    _("Timestamp"): [p.formatted_timestamp or "" for p in pages],
}

st.dataframe(
    df,
    column_config={
        _("URL"): st.column_config.LinkColumn(
            _("URL"), display_text="Open page", max_chars=40
        ),
    },
)
