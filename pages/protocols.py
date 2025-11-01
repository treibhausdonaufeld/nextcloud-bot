from datetime import datetime
from typing import List, cast

import streamlit as st
from google import genai

from lib.couchdb import couchdb
from lib.menu import menu
from lib.nextcloud.models.collective_page import CollectivePage
from lib.nextcloud.models.protocol import Protocol, get_protocol_collection
from lib.nextcloud.models.user import NCUserList
from lib.settings import _, settings
from lib.streamlit_oauth import load_user_data


def prompt_ai(protocols: List[Protocol], question: str) -> str:
    context = "\n\n".join(
        [
            f"Datum: {p.date}\nGruppe: {p.group_name}\nInhalt: {p.page.content}"
            for p in protocols
            if p.page and p.page.content
        ]
    )

    prompt = f"""Du bist ein hilfreicher Assistent, der Protokolle von Meetings zusammenfasst und Fragen dazu beantwortet.
    Nutze den folgenden Kontext, um die Frage zu beantworten. Wenn die Information nicht im Kontext vorhanden ist, antworte mit "Die Information ist nicht verfügbar".

    Kontext:
    {context}

    Frage: {question}
    Antwort:"""

    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
    )

    return response.text


def display_users(user_ids: list[str]):
    names = []
    for user_id in user_ids:
        user = user_list.get_user_by_uid(user_id)
        displayname = user_id
        if user and user.ocs and user.ocs.displayname:
            displayname = user.ocs.displayname
        names.append(displayname)
    return ", ".join(names)


@st.cache_data(ttl=3600)
def get_all_protocols() -> List[Protocol]:
    return cast(List[Protocol], Protocol.get_all())


@st.cache_data(ttl=3600)
def groups_with_count() -> List[str]:
    # group all protocols by group name and count them
    groups = list(set(p.group_name for p in get_all_protocols() if p.group_name))
    groups.sort()
    return groups


# Streamlit app starts here
title = _("Protocols").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="📝")

menu()
load_user_data()

db = couchdb()

user_list = NCUserList()

st.title(title)

protocol_collection = get_protocol_collection()


# filter out protocols in the future
now_str = datetime.now().strftime("%Y-%m-%d")


col1, col2, col3 = st.columns((1, 3, 1))
selected_group = col1.selectbox(
    label=_("Filter by group"),
    options=([""] + groups_with_count()),
    placeholder=_("Select a group"),
)
query_text = col2.text_input(_("Search protocols"), "")
ai_enabled = col3.checkbox(
    _("Use AI to answer"),
    value=True if settings.gemini_api_key else False,
    disabled=not settings.gemini_api_key,
)

if selected_group and not query_text:
    protocols = [p for p in get_all_protocols() if p.group_name == selected_group]
elif query_text:
    results = protocol_collection.query(
        query_texts=[query_text],
        n_results=10 if not ai_enabled else 5,
        where={"group_name": selected_group} if selected_group else None,
    )

    result_ids = results["ids"][0]
    protocols = [p for p in get_all_protocols() if p.id in result_ids]

    if ai_enabled:
        answer = prompt_ai(protocols, query_text)
        st.markdown(f"### {_('Answer')}:")
        st.markdown(answer)
        protocols = []
else:
    # sort by parsed date (fallback to string) descending
    protocols = sorted(
        [p for p in get_all_protocols() if p.date <= now_str],
        key=lambda p: p.date,
        reverse=True,
    )

for protocol in protocols:
    with st.expander(f"{protocol.date} - {protocol.group_name}"):
        # show link to the protocol page when available
        col1, col2 = st.columns([1, 3])

        page = None
        try:
            if protocol.page_id:
                page = CollectivePage.get_from_page_id(protocol.page_id)
        except Exception:
            page = None

        if page and getattr(page, "url", None):
            col1.markdown(f"**{_('Link')}:** [{protocol.protocol_path}]({page.url})")
        else:
            col1.markdown(f"**{_('Path')}:** `{protocol.protocol_path}`")
        col1.markdown(
            f"**{_('Moderated by')}:** {display_users(protocol.moderated_by)}"
        )
        col1.markdown(f"**{_('Protocol by')}:** {display_users(protocol.protocol_by)}")
        col1.markdown(
            f"**{_('Participants')}:** {display_users(protocol.participants)}"
        )

        if protocol.page:
            col2.markdown(protocol.page.content)
