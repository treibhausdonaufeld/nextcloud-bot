import random
from datetime import datetime
from gettext import gettext as _

import pandas as pd
import pytz
import streamlit as st

from lib.couchdb import couchdb
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
st.set_page_config(page_title=title, page_icon="🏡", layout="wide")

menu()
load_user_data()

db = couchdb()


if "user_data" in st.session_state:
    title = get_greeting() + ", " + st.session_state.user_data["name"]

st.title(title)

view_result = db.query("mentions/by_user", group=True)

distinct_users = []
for row in view_result:
    distinct_users.append({"username": row["key"], "total_mentions": row["value"]})

st.dataframe(distinct_users)

user = st.selectbox("Select a user", options=[u["username"] for u in distinct_users])
st.write(user)

if user:
    user_view_result = db.query(
        "mentions/by_user", key=user, reduce=False, include_docs=True
    )

    user_docs = []
    for row in user_view_result:
        user_docs.append({"doc": row["doc"]})

    st.dataframe(user_docs)


# @st.cache_data(ttl=60)
def load_collective_pages(limit: int = 10):
    """Load collective_page documents from CouchDB.

    Returns a list of dicts with keys: _id, title, url, content, created_at, modified_at
    """

    # Use a simple Mango query if supported; otherwise fallback to all docs filter
    lookup = {
        "selector": {"type": "collective_page"},
        "sort": [{"timestamp": "desc"}],
        "limit": limit,
    }
    response, results = db.resource.post("_find", json=lookup)
    response.raise_for_status()
    docs = results.get("docs", []) if isinstance(results, dict) else []

    return docs


# Display collected pages on the start page
collective_pages = load_collective_pages()
st.subheader("Newest Updates from Collectives")

if collective_pages:
    for p in collective_pages:
        dt_object = datetime.fromtimestamp(p.get("timestamp"))
        tz = pytz.timezone(settings.timezone)
        localized_dt = tz.localize(dt_object)

        with st.expander(
            f"{localized_dt.strftime('%c')} - " + p.get("title"),
            expanded=False,
        ):
            collective_name = p["raw"]["collectivePath"].split("/")[1]
            url = (
                str(settings.nextcloud.base_url)
                + f"apps/collectives/{collective_name}-{settings.nextcloud.collectives_id}"
                + f"/{p['raw']['slug']}-{p['raw']['id']}"
            )
            content = (p.get("content") or "").strip()
            excerpt = content[:600] + ("…" if len(content) > 600 else "")

            st.markdown(f"## [{p.get('title')}]({url})")
            if excerpt:
                st.write(excerpt)

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

    login()
