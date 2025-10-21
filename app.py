import random
from datetime import datetime
from gettext import gettext as _

import pandas as pd
import streamlit as st

from lib.menu import menu
from lib.settings import (
    available_languages,
    set_language,
    settings,
)
from lib.streamlit_oauth import load_user_data, login

pd.set_option("display.float_format", "{:.2f}".format)


def get_greeting():
    greetings = {
        "morning": [
            "Guten Morgen",
            "Morgen",
            "Schönen guten Morgen",
            "Einen wunderschönen guten Morgen",
            "Hallo, guten Morgen",
        ],
        "afternoon": [
            "Guten Tag",
            "Mahlzeit",
            "Moizeit",
            "Seas",
            "Grias di",
            "Einen wunderschönen guten Tag",
            "Hallo, guten Tag",
        ],
        "evening": [
            "Guten Abend",
            "Abend",
            "Seas",
            "Einen wunderschönen guten Abend",
            "Hallo, guten Abend",
        ],
        "night": [
            "Gute Nacht",
            "Nacht",
            "Schlaf gut",
            "Schönen guten Abend noch",
            "Hallo, gute Nacht",
        ],
    }

    current_hour = datetime.now().hour

    if 5 <= current_hour < 10:
        period = "morning"
    elif 10 <= current_hour < 16:
        period = "afternoon"
    elif 16 <= current_hour < 22:
        period = "evening"
    else:
        period = "night"

    greeting = random.choice(greetings[period])
    return greeting


# Streamlit app starts here
title = _("{common_name} Dashboard").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="🚲")

menu()
load_user_data()

if "user_data" in st.session_state:
    title = get_greeting() + ", " + st.session_state.user_data["name"]

st.title(title)

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

    if selected_language_key != st.session_state.language:
        st.session_state.language = selected_language_key
        set_language(selected_language_key)
        st.rerun()

    login()
