from datetime import datetime
from typing import Generator, List, cast

import streamlit as st
from chromadb.api.types import Where
from google import genai
from langchain_chroma import Chroma

from lib.chromadb import UNIFIED_COLLECTION_NAME, chroma_client, langchain_embeddings
from lib.couchdb import couchdb
from lib.menu import menu
from lib.nextcloud.models.collective_page import (
    CollectivePage,
    PageSubtype,
)
from lib.nextcloud.models.protocol import Protocol
from lib.nextcloud.models.user import NCUserList
from lib.settings import _, settings
from lib.streamlit_oauth import load_user_data


def prompt_ai(protocols: List[Protocol], question: str) -> Generator[str, None, None]:
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
    # Stream the response
    for chunk in client.models.generate_content_stream(
        model=settings.gemini_model,
        contents=prompt,
    ):
        if chunk.text:
            yield chunk.text


def display_users(user_ids: list[str]):
    names = []
    for user_id in user_ids:
        try:
            names.append(str(user_list[user_id]))
        except KeyError:
            names.append(user_id)
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

# Create LangChain Chroma vectorstore for semantic search
vectorstore = Chroma(
    client=chroma_client,
    collection_name=UNIFIED_COLLECTION_NAME,
    embedding_function=langchain_embeddings,
)

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
    # Build where clause to filter by source_type="page", subtype=PROTOCOL, and optionally group
    where_clause: Where = cast(
        Where,
        {
            "source_type": "page",
            "subtype": PageSubtype.PROTOCOL.value,
        },
    )
    if selected_group:
        where_clause = cast(
            Where,
            {
                "subtype": PageSubtype.PROTOCOL.value,
                "group_name": selected_group,
            },
        )

    # Use LangChain semantic search
    retriever = vectorstore.as_retriever(
        search_type="similarity",
        search_kwargs={
            "k": 10 if not ai_enabled else 5,
            "where": where_clause,
        },
    )

    # Retrieve relevant documents
    results = retriever.invoke(query_text)

    # Extract page IDs from metadata
    result_page_ids = list(
        set(
            doc.metadata.get("page_id")
            for doc in results
            if doc.metadata.get("page_id")
        )
    )

    protocols = [p for p in get_all_protocols() if p.page_id in result_page_ids]

    if ai_enabled:
        st.markdown(f"### {_('Answer')}:")
        st.write_stream(prompt_ai(protocols, query_text))
        protocols = []
else:
    # sort by parsed date (fallback to string) descending
    protocols = sorted(
        [p for p in get_all_protocols() if p.date <= now_str],
        key=lambda p: p.date,
        reverse=True,
    )

if protocols:
    # Create dataframe with protocol information
    protocol_data = []
    for protocol in protocols:
        page = None
        try:
            if protocol.page_id:
                page = CollectivePage.get_from_page_id(protocol.page_id)
        except Exception:
            page = None

        # Create link to protocol
        if page and getattr(page, "url", None):
            link = page.url
        else:
            link = ""

        protocol_data.append(
            {
                _("Date"): protocol.date,
                _("Group"): protocol.group_name,
                _("Title"): page.title if page else "",
                _("Moderated by"): display_users(protocol.moderated_by),
                _("Protocol by"): display_users(protocol.protocol_by),
                _("Participants"): display_users(protocol.participants),
                _("Link"): link,
            }
        )

    st.dataframe(
        protocol_data,
        column_config={
            _("Link"): st.column_config.LinkColumn(
                _("Link"),
                display_text=_("Open"),
            ),
        },
        width="stretch",
        hide_index=True,
        height=600,
    )
