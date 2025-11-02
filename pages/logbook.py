import re
from typing import List, cast

import streamlit as st
from chromadb import WhereDocument

from lib.logbook_xlsx_import import import_decisions_from_excel
from lib.menu import menu
from lib.nextcloud.models.decision import Decision, get_decision_collection
from lib.nextcloud.models.group import Group
from lib.settings import (
    _,
    settings,
)
from lib.streamlit_oauth import load_user_data


@st.cache_data(ttl=10)
def get_group_names() -> List[str]:
    group_names = [g.name for g in cast(list[Group], Group.get_all())]
    group_names.sort()
    return group_names


# @st.cache_data(ttl=3600)
def get_all_decisions(
    limit: int, skip: int, selector: dict | None = None
) -> List["Decision"]:
    return Decision.paginate(limit, skip, sort=[{"date": "desc"}], selector=selector)


# Streamlit app starts here
title = _("Logbook").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="✅", layout="wide")

menu()
load_user_data()

st.title(title)

filter_container = st.container()
df_container = st.container()
pagination_container = st.container()

# Add filter for group
col1, col2, col3 = filter_container.columns((1, 3, 3))
selected_group = col1.selectbox(
    label=_("Filter by group"),
    options=([""] + get_group_names()),
    placeholder=_("Select a group"),
    on_change=lambda: st.session_state.update({"current_page": 1}),
)

search_text = col2.text_input(
    _("Search"),
    "",
    on_change=lambda: st.session_state.update({"current_page": 1}),
)
search_type = col3.radio(
    _("Search Type"),
    options=[_("Semantic"), _("Any"), _("All"), _("Exact")],
    captions=[_("Semantic Search"), _("Any word"), _("All words"), _("Exact Match")],
    index=0,
    horizontal=True,
    on_change=lambda: st.session_state.update({"current_page": 1}),
)

## fetch data
distances = []

page_size = st.session_state.get("page_size", 25)
current_page = st.session_state.get("current_page", 0)
if not current_page:
    st.session_state["current_page"] = current_page = 1


if search_text:
    decision_collection = get_decision_collection()

    query_kwargs = {
        "where": {"group_name": selected_group} if selected_group else None,
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
            limit=page_size,
            offset=page_size * (current_page - 1),
            where_document=cast(WhereDocument, where_document)
            if where_document
            else None,
        )
        result_ids = results["ids"]

    elif search_type == _("Semantic"):
        results = decision_collection.query(
            query_texts=[search_text],
            n_results=page_size,
        )

        result_ids = results["ids"][0]
        if results["distances"]:
            distances = results["distances"][0]

    decisions = [cast(Decision, Decision.get(id)) for id in result_ids]
    total_count = len(decisions)
else:
    decisions = get_all_decisions(
        limit=page_size,
        skip=page_size * (current_page - 1),
        selector={"group_name": selected_group} if selected_group else None,
    )

if not search_text:
    decisions = sorted(decisions, key=lambda p: p.date, reverse=True)


df = {
    _("Date"): [d.date for d in decisions],
    _("Title"): [d.title for d in decisions],
    _("Text"): [d.text for d in decisions],
    _("Valid Until"): [d.valid_until for d in decisions],
    _("Group"): [d.group_name for d in decisions],
    _("Objections"): [d.objections for d in decisions],
    _("Link"): [d.page.url if d.page else d.external_link or "" for d in decisions],
}
if distances:
    df[_("Distance")] = distances

st.dataframe(
    df,
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
    height=500,
)

st.markdown("**" + _("Page") + "**: " + str(current_page))
flex = st.container(horizontal=True, horizontal_alignment="right")

page_size = flex.selectbox(
    _("Page size"), options=[10, 25, 50, 100], key="page_size", index=1, width=140
)
# total_pages = int(total_count / page_size) if int(total_count / page_size) > 0 else 1
current_page = flex.number_input(
    "Page", min_value=1, max_value=100, step=1, key="current_page", width=140
)

if current_page > 1:
    flex.button(
        "Previous Page",
        on_click=lambda: st.session_state.update({"current_page": current_page - 1}),
    )
if len(decisions) >= page_size:
    flex.button(
        "Next Page",
        on_click=lambda: st.session_state.update({"current_page": current_page + 1}),
    )


# bottom_menu = st.columns((4, 1, 1))
# with bottom_menu[2]:
#     page_size = flex.selectbox("Page Size", options=[10, 25, 50, 100], key="page_size")
# with bottom_menu[1]:
#     total_pages = (
#         int(total_count / page_size) if int(total_count / page_size) > 0 else 1
#     )
#     current_page = flex.number_input(
#         "Page", min_value=1, max_value=total_pages, step=1, key="current_page"
#     )
# with bottom_menu[0]:
#     st.markdown(f"Page **{current_page}** of **{total_pages}** ")

# pages = split_frame(dataset, batch_size)
# pagination.dataframe(data=pages[current_page - 1], use_container_width=True)

st.divider()

# XLSX Upload Section
uploaded_file = st.file_uploader(
    _("Upload XLSX file with previous decisions to import"), type=["xlsx"]
)

if uploaded_file is not None:
    try:
        created, erros = import_decisions_from_excel(uploaded_file)

        if created:
            st.success(f"Successfully imported {created} decisions!")
        if erros:
            st.error("Errors encountered:")
            for error in erros:
                st.write(f"- {error}")

    except Exception as e:
        st.error(f"Error reading file: {str(e)}")
