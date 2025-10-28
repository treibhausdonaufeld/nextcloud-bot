import time
from gettext import gettext as _

import streamlit as st
from streamlit_cookies_controller import CookieController
from streamlit_js_eval import get_browser_language

from lib.settings import set_language, settings


def menu():
    if "user_data" not in st.session_state:
        st.session_state.controller = CookieController()
        time.sleep(0.5)

    language = st.session_state.language = st.session_state.get(
        "language", (get_browser_language() or settings.default_language)[0:2]
    )

    set_language(language)

    st.sidebar.page_link("app.py", label="🏠 " + _("Home"))
    st.sidebar.page_link("pages/users.py", label="👥 " + _("Users"))
    st.sidebar.page_link("pages/groups.py", label="⭕ " + _("Groups"))
    st.sidebar.page_link("pages/protocols.py", label="📝 " + _("Protocols"))
    st.sidebar.page_link("pages/logbook.py", label="✅ " + _("Logbook"))
