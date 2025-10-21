import time
from gettext import gettext as _

import streamlit as st
from streamlit_cookies_controller import CookieController
from streamlit_js_eval import get_browser_language

from lib.common import set_language, settings


def menu():
    if "user_data" not in st.session_state:
        st.session_state.controller = CookieController()
        time.sleep(0.5)

    language = st.session_state.language = st.session_state.get(
        "language", (get_browser_language() or settings.default_language)[0:2]
    )

    set_language(language)

    st.sidebar.page_link("app.py", label=_("🏠 Home"))
