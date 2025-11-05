import time

import streamlit as st
from streamlit_cookies_controller import CookieController
from streamlit_js_eval import get_browser_language

from lib.settings import (
    _,
    available_languages,
    set_language,
    settings,
)


def menu():
    if "user_data" not in st.session_state:
        st.session_state.controller = CookieController()
        time.sleep(0.5)

    language = st.session_state.language = st.session_state.get(
        "language", (get_browser_language() or settings.default_language)[0:2]
    )

    set_language(language)

    st.sidebar.page_link("app.py", label="🏠 " + _("Home"))
    st.sidebar.page_link("pages/groups.py", label="⭕ " + _("Groups"))
    st.sidebar.page_link("pages/timeline.py", label="⌛ " + _("Timeline"))
    st.sidebar.page_link("pages/protocols.py", label="📝 " + _("Protocols"))
    st.sidebar.page_link("pages/logbook.py", label="✅ " + _("Logbook"))
    st.sidebar.page_link("pages/collective_pages.py", label=_("Pages"))

    with st.sidebar:
        # if "language" not in st.session_state:
        #     st.session_state.language = get_browser_language() or default_language
        # language = st.session_state.language

        selected_language = st.selectbox(
            _("Language"),
            available_languages.values(),
            index=list(available_languages.keys()).index(st.session_state.language),
        )

        selected_language_key = {v: k for k, v in available_languages.items()}.get(
            selected_language
        )

        if selected_language_key and selected_language_key != st.session_state.language:
            st.session_state.language = selected_language_key
            set_language(selected_language_key)
            st.rerun()
