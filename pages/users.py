from gettext import gettext as _

import streamlit as st

from lib.couchdb import couchdb
from lib.menu import menu
from lib.nextcloud.models.collective_page import CollectivePage
from lib.nextcloud.nc_users import NCUserList
from lib.settings import (
    available_languages,
    set_language,
    settings,
)
from lib.streamlit_oauth import load_user_data, login

# Streamlit app starts here
title = _("Users").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="👥", layout="wide")

menu()
load_user_data()

db = couchdb()

user_list = NCUserList()

st.title(title)

st.subheader(_("Users and their mentioned pages"))

view_result = db.query("mentions/by_user", group=True)

distinct_users = []
for row in view_result:
    distinct_users.append({"username": row["key"], "total_mentions": row["value"]})

st.dataframe(distinct_users)

st.subheader(_("All users in Nextcloud"))
st.dataframe([user.__dict__ for user in user_list.users])

st.subheader(_("Pages mentioned by user"))

user = st.selectbox("Select a user", options=[u["username"] for u in distinct_users])
st.write(user)

if user:
    user_view_result = db.query(
        "mentions/by_user", key=user, reduce=False, include_docs=True
    )
    for row in user_view_result:
        page = CollectivePage(**row["doc"])
        st.markdown(f"### [{page.title}]({page.url})")


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
