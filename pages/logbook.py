import re
from typing import List, cast

import pandas as pd
import streamlit as st
from chromadb import Where, WhereDocument

from lib.chromadb import get_unified_collection
from lib.logbook_xlsx_import import import_decisions_from_excel
from lib.menu import menu
from lib.nextcloud.models.decision import Decision
from lib.nextcloud.models.group import Group
from lib.settings import _, settings
from lib.streamlit_oauth import load_user_data


@st.cache_data(ttl=300)
def get_group_names() -> List[str]:
    group_names = [g.name for g in cast(list[Group], Group.get_all())]
    group_names.sort()
    return group_names


@st.cache_data(ttl=3600)
def get_all_decisions(selector: dict | None = None) -> List["Decision"]:
    """Get all decisions without pagination."""
    return Decision.get_all(selector=selector, limit=10_000)


# Streamlit app starts here
title = _("Logbook").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="✅", layout="wide")

menu()
load_user_data()

st.title(title)

filter_container = st.container()

# Add filter for group
col1, col2, col3 = filter_container.columns((1, 3, 3))
selected_group = col1.selectbox(
    label=_("Filter by group"),
    options=([""] + get_group_names()),
    placeholder=_("Select a group"),
)

search_text = col2.text_input(
    _("Search"),
    "",
)
search_type = col3.radio(
    _("Search Type"),
    options=[_("Semantic"), _("Any"), _("All"), _("Exact")],
    captions=[_("Semantic Search"), _("Any word"), _("All words"), _("Exact Match")],
    index=0,
    horizontal=True,
)

## fetch data
distances = []


if search_text:
    decision_collection = get_unified_collection()

    # Build where clause to filter by source_type = "decision" and optionally by group
    where_clause: dict[str, str] = {"source_type": Decision.__name__}
    if selected_group:
        where_clause["group_name"] = selected_group
    where_clause_typed = cast(Where, where_clause)

    query_kwargs = {
        "where": where_clause_typed,
    }
    if search_type in (_("Any"), _("All"), _("Exact")):
        where_document = None
        if search_type == _("Exact") or len(search_text.split()) <= 1:
            where_document = {"$regex": rf"(?i){re.escape(search_text)}"}
        elif search_text:
            condition = "$and" if search_type == _("All") else "$or"
            where_document = {
                condition: [  # type: ignore
                    {"$regex": rf"(?i){re.escape(word)}"}
                    for word in search_text.split()
                ]
            }

        results = decision_collection.get(
            where=where_clause_typed,
            where_document=cast(WhereDocument, where_document)
            if where_document
            else None,
        )
        result_ids = results["ids"]

    elif search_type == _("Semantic"):
        results = decision_collection.query(
            query_texts=[search_text],
            n_results=100,  # Get top 100 results for semantic search
            where=where_clause_typed,
        )

        result_ids = results["ids"][0]
        if results["distances"]:
            distances = results["distances"][0]

    decisions = [cast(Decision, Decision.get(id)) for id in result_ids]
    total_count = len(decisions)
else:
    decisions = get_all_decisions(
        selector={"group_name": selected_group} if selected_group else None,
    )

if not search_text:
    decisions = sorted(decisions, key=lambda p: p.date, reverse=True)


df_display = {
    _("Date"): [d.date for d in decisions],
    _("Group"): [d.group_name for d in decisions],
    _("Title"): [d.title for d in decisions],
    _("Text"): [d.text for d in decisions],
    _("Valid Until"): [d.valid_until for d in decisions],
    _("Objections"): [d.objections for d in decisions],
    _("Link"): [d.page.url if d.page else d.external_link or "" for d in decisions],
}
if distances:
    df_display[_("Distance")] = distances

st.dataframe(
    df_display,
    column_config={
        _("Date"): st.column_config.DateColumn(
            _("Date"),
            format="YYYY-MM-DD",
        ),
        _("Text"): st.column_config.TextColumn(_("Text"), max_chars=100),
        _("Link"): st.column_config.LinkColumn(
            _("Link"), display_text=_("Open protocol"), max_chars=30
        ),
    },
    hide_index=False,
    height=600,
    width="stretch",
)

st.divider()

# XLSX Upload Section
uploaded_file = st.file_uploader(
    _("Upload XLSX file with previous decisions to import"), type=["xlsx"]
)

if uploaded_file is not None:
    try:
        created_count = 0
        errors = []

        # Read the Excel file
        df = pd.read_excel(uploaded_file)
        total_rows = len(df)

        # Create progress bar and status text
        progress_bar = st.progress(0.0, _("Importing file..."))

        # Process the iterator
        for idx, result in enumerate(import_decisions_from_excel(df), start=1):
            # Update progress
            progress = idx / total_rows
            progress_bar.progress(progress)

            if result == "":
                # Success - empty string
                created_count += 1
            else:
                # Error message
                errors.append(result)

        # Clear progress indicators
        progress_bar.empty()

        if created_count > 0:
            st.success(f"Successfully imported {created_count} decisions!")
        if errors:
            st.error("Errors encountered:")
            for error in errors:
                st.write(f"- {error}")

    except Exception as e:
        st.error(f"Error reading file: {str(e)}")
