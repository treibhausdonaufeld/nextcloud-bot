from datetime import datetime
from typing import Generator, List, cast

import pandas as pd
import streamlit as st
from chromadb.api.types import Where
from google import genai

from lib.chromadb import get_unified_collection
from lib.couchdb import couchdb
from lib.menu import menu
from lib.nextcloud.models.collective_page import (
    CollectivePage,
    PageSubtype,
)
from lib.nextcloud.models.group import Group
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
st.set_page_config(page_title=title, page_icon="📝", layout="wide")

menu()
load_user_data()

db = couchdb()

user_list = NCUserList()

st.title(title)

# Get ChromaDB collection for semantic search
collection = get_unified_collection()

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

    # Use ChromaDB semantic search directly
    results = collection.query(
        query_texts=[query_text],
        n_results=10 if not ai_enabled else 5,
        where=where_clause,
    )

    # Extract page IDs from metadata
    metadatas = results.get("metadatas", [[]])
    result_page_ids = list(
        set(
            metadata.get("page_id")
            for metadata in (metadatas[0] if metadatas else [])
            if metadata and metadata.get("page_id")
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

        # Create link to protocol and title for display
        if page and getattr(page, "url", None):
            # Append title as URL fragment so we can extract it with regex
            # Use #title: prefix to make it extractable
            link = f"{page.url}#title:{page.title}"
            title = page.title
        else:
            link = ""
            title = ""

        protocol_data.append(
            {
                _("Date"): protocol.date,
                _("Title"): link if link else title,
                _("Group"): protocol.group_name,
                _("AI Summary"): protocol.ai_summary or "",
                _("Moderated by"): display_users(protocol.moderated_by),
                _("Protocol by"): display_users(protocol.protocol_by),
                _("Participants"): display_users(protocol.participants),
            }
        )

        if not settings.gemini_api_key:
            protocol_data[-1].pop(_("AI Summary"))

    column_config = {
        _("Title"): st.column_config.LinkColumn(
            _("Title"),
            display_text=r".*#title:(.*)",
            width="medium",
        )
    }
    if settings.gemini_api_key:
        column_config.update(
            {
                _("AI Summary"): st.column_config.TextColumn(
                    _("AI Summary"),
                    help=_("Click to expand"),
                ),
            }
        )

    st.dataframe(
        protocol_data, column_config=column_config, width="stretch", hide_index=True
    )

    # Show member statistics if a group is selected
    if selected_group:
        st.markdown(f"### {_('Member Statistics for')} {selected_group}")

        try:
            group = Group.get_by_name(selected_group)
            all_group_members = group.all_members
        except ValueError:
            all_group_members = []

        # Get all protocols for the selected group
        group_protocols = [
            p for p in get_all_protocols() if p.group_name == selected_group
        ]

        # Initialize statistics for all group members
        user_stats = {
            user_id: {"moderated": 0, "protocol": 0, "attended": 0}
            for user_id in all_group_members
        }

        # Collect statistics per user from protocols
        for protocol in group_protocols:
            # Count moderators
            for user_id in protocol.moderated_by:
                if user_id in user_stats:
                    user_stats[user_id]["moderated"] += 1

            # Count protocol writers
            for user_id in protocol.protocol_by:
                if user_id in user_stats:
                    user_stats[user_id]["protocol"] += 1

            # Count participants
            for user_id in protocol.participants:
                if user_id in user_stats:
                    user_stats[user_id]["attended"] += 1

        # Create dataframe for member statistics
        member_data: List[dict[str, str | int]] = []
        for user_id, stats in user_stats.items():
            try:
                user_name = str(user_list[user_id])
            except KeyError:
                user_name = user_id

            # Calculate score: moderation=2, protocol=2, attendance=1
            score = (
                (stats["moderated"] * 2)
                + (stats["protocol"] * 2)
                + (stats["attended"] * 1)
            )

            member_data.append(
                {
                    _("Member"): user_name,
                    _("Moderated"): stats["moderated"],
                    _("Wrote Protocol"): stats["protocol"],
                    _("Attended"): stats["attended"],
                    _("Score"): score,
                }
            )

        # Sort by score descending by default
        member_data.sort(key=lambda x: cast(int, x[_("Score")]), reverse=True)

        st.dataframe(member_data, width="stretch", hide_index=True)

        # Show bar chart with scores
        st.markdown(f"#### {_('Member Scores')}")

        sort_by_score = st.checkbox(_("Sort by score (with position)"), value=True)

        df = pd.DataFrame(member_data)

        if sort_by_score:
            # Sort by score descending and prepend position to name
            df_sorted = df.sort_values(by=_("Score"), ascending=False).copy()
            total_count = len(df_sorted)
            width = len(str(total_count))
            df_sorted[_("Member")] = [
                f"{str(i + 1).zfill(width)}. {name}"
                for i, name in enumerate(df_sorted[_("Member")])
            ]
        else:
            # Sort by name alphabetically
            df_sorted = df.sort_values(by=_("Member"), ascending=True)

        st.bar_chart(df_sorted.set_index(_("Member"))[_("Score")], horizontal=True)
