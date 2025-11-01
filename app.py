import random
from datetime import datetime
from typing import Sequence, cast

import pandas as pd
import streamlit as st

from lib.couchdb import couchdb
from lib.menu import menu
from lib.nextcloud.models.collective_page import CollectivePage
from lib.settings import (
    _,
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
st.set_page_config(page_title=title, page_icon="🏡", layout="wide")

menu()
load_user_data()

db = couchdb()


if "user_data" in st.session_state:
    title = get_greeting() + ", " + st.session_state.user_data["name"]

st.title(title)


# Display collected pages on the start page
collective_pages = cast(
    Sequence[CollectivePage],
    CollectivePage.get_all(limit=10, sort=[{"ocs.timestamp": "desc"}]),
)
st.subheader(_("Newest Updates"))

for p in collective_pages:
    with st.expander(
        f"{p.formatted_timestamp} - " + (p.title or "Untitled"),
        expanded=False,
    ):
        url = p.url
        content = (p.content or "").strip()
        excerpt = content[:1000] + ("…" if len(content) > 1000 else "")

        if url:
            st.markdown(f"## [{p.title}]({url})")
        else:
            st.markdown(f"## {p.title}")

        if excerpt:
            st.text(excerpt)

st.dataframe([p.ocs.model_dump() for p in collective_pages])

with st.sidebar:
    login()
