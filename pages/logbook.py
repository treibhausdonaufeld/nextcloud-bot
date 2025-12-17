from typing import List, cast

import pandas as pd
import streamlit as st
from chromadb import Where

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
    decisions = Decision.get_all(selector=selector, limit=10_000)
    return sorted(decisions, key=lambda p: p.date, reverse=True)


# Streamlit app starts here
title = _("Logbook").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="✅", layout="wide")

menu()
load_user_data()

st.title(title)

filter_container = st.container()

# Add filter for group
col1, col2, col3 = filter_container.columns((2, 2, 1))
selected_group = col1.selectbox(
    label=_("Filter by group"),
    options=([""] + get_group_names()),
    placeholder=_("Select a group"),
)

search_text = col2.text_input(
    _("Search"),
    "",
)
search_type = col3.selectbox(
    label=_("Search Type"),
    options=[_("Semantic"), _("Any"), _("All"), _("Exact")],
    # captions=[_("Semantic Search"), _("Any word"), _("All words"), _("Exact Match")],
    index=1,
    # horizontal=True,
)

## fetch data
distances = []


def matches_search(decision: Decision, search_text: str, search_type: str) -> bool:
    """Check if a decision matches the search criteria in title, text, or objections."""
    searchable_text = " ".join(
        [decision.title or "", decision.text or "", decision.objections or ""]
    ).lower()

    search_lower = search_text.lower()

    if search_type == _("Exact"):
        return search_lower in searchable_text
    elif search_type == _("All"):
        words = search_lower.split()
        return all(word in searchable_text for word in words)
    elif search_type == _("Any"):
        words = search_lower.split()
        return any(word in searchable_text for word in words)
    return False


decisions = None
if search_text:
    if search_type in (_("Any"), _("All"), _("Exact")):
        # Search directly in Decision fields
        all_decisions = get_all_decisions(
            selector={"group_name": selected_group} if selected_group else None,
        )
        decisions = [
            d for d in all_decisions if matches_search(d, search_text, search_type)
        ]
    elif search_type == _("Semantic"):
        decision_collection = get_unified_collection()

        # Build where clause to filter by source_type = "decision" and optionally by group
        where_clause: dict[str, str] = {"source_type": Decision.__name__}
        if selected_group:
            where_clause["group_name"] = selected_group
        where_clause_typed = cast(Where, where_clause)

        results = decision_collection.query(
            query_texts=[search_text],
            n_results=100,  # Get top 100 results for semantic search
            where=where_clause_typed,
        )

        result_ids = results["ids"][0]
        if results["distances"]:
            distances = results["distances"][0]

        decisions = [cast(Decision, Decision.get(id)) for id in result_ids]
else:
    decisions = get_all_decisions(
        selector={"group_name": selected_group} if selected_group else None,
    )


def truncate_text(text: str | None, max_length: int = 300) -> str:
    """Truncate text to max_length characters with ellipsis."""
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[:max_length].rsplit(" ", 1)[0] + "..."


@st.dialog(_("Decision Details"), width="large")
def show_decision_details(decision_id: str | None, distance: float | None = None):
    """Display full decision details in a popup dialog.

    Accept a possibly None decision_id (the calling code may pass None). Guard
    against non-string or missing ids and handle lookup errors gracefully so
    the dialog opens quickly and doesn't raise.
    """
    if not decision_id:
        st.error(_("Decision not found"))
        return

    # Fetch decision by ID for fast popup loading. Be defensive: Decision.get
    # may raise or return None in tests/mocks.
    try:
        decision = cast(Decision, Decision.get(decision_id))
    except Exception:
        decision = None

    if not decision:
        st.error(_("Decision not found"))
        return

    st.markdown(f"# {decision.title or _('No Title')}")

    link = decision.page.url if decision.page else decision.external_link

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**{_('Date')}:** {decision.date}")
        st.markdown(f"**{_('Group')}:** {decision.group_name or '-'}")
    with col2:
        st.markdown(f"**{_('Valid Until')}:** {decision.valid_until or '-'}")
        if link:
            st.link_button(_("Open protocol"), link)

    st.markdown(f"### {_('Text')}")
    st.markdown(decision.text or _("No text available"))

    if decision.objections:
        st.markdown(f"### {_('Objections')}")
        st.markdown(decision.objections)


def render_decision_card(decision: Decision, idx: int, distance: float | None = None):
    """Render a single decision card."""
    with st.container(border=True):
        col1, col2 = st.columns([4, 1])

        with col1:
            # Title and metadata
            title_text = decision.title or _("No Title")
            st.markdown(f"#### {title_text}")

            meta_parts = []
            if decision.date:
                meta_parts.append(f"📅 {decision.date}")
            if decision.group_name:
                meta_parts.append(f"👥 {decision.group_name}")
            if decision.valid_until:
                meta_parts.append(f"⏰ {_('Valid until')}: {decision.valid_until}")
            if distance is not None:
                meta_parts.append(f"📊 {_('Distance')}: {distance:.4f}")

            st.caption(" | ".join(meta_parts))

            # Truncated text
            truncated = truncate_text(decision.text, 300)
            if truncated:
                st.markdown(truncated)

        with col2:
            if st.button(_("View Details"), key=f"btn_{idx}", use_container_width=True):
                show_decision_details(decision.id, distance)

            link = decision.page.url if decision.page else decision.external_link
            if link:
                st.link_button(_("Open Link"), link, use_container_width=True)


# Display decisions as HTML cards with pagination
if not decisions:
    st.info(_("No decisions found."))
else:
    total_decisions = len(decisions)

    # Pagination settings
    ITEMS_PER_PAGE_OPTIONS = [10, 20, 50, 100]
    DEFAULT_ITEMS_PER_PAGE = 20

    # Initialize session state for pagination
    if "page_number" not in st.session_state:
        st.session_state.page_number = 0
    if "items_per_page" not in st.session_state:
        st.session_state.items_per_page = DEFAULT_ITEMS_PER_PAGE

    # Pagination controls at top
    col_info, col_per_page, col_nav = st.columns([2, 1, 2])

    with col_per_page:
        items_per_page = st.selectbox(
            _("Items per page"),
            options=ITEMS_PER_PAGE_OPTIONS,
            index=ITEMS_PER_PAGE_OPTIONS.index(st.session_state.items_per_page),
            key="items_per_page_select",
        )
        if items_per_page != st.session_state.items_per_page:
            st.session_state.items_per_page = items_per_page
            st.session_state.page_number = 0
            st.rerun()

    # Calculate pagination
    total_pages = (
        total_decisions + st.session_state.items_per_page - 1
    ) // st.session_state.items_per_page

    # Ensure page number is valid
    if st.session_state.page_number >= total_pages:
        st.session_state.page_number = max(0, total_pages - 1)

    start_idx = st.session_state.page_number * st.session_state.items_per_page
    end_idx = min(start_idx + st.session_state.items_per_page, total_decisions)

    with col_info:
        st.markdown(
            f"**{total_decisions}** {_('decisions found')} | {_('Showing')} {start_idx + 1}-{end_idx}"
        )

    with col_nav:
        nav_col1, nav_col2, nav_col3, nav_col4, nav_col5 = st.columns(5)

        with nav_col1:
            if st.button(
                "⏮️", disabled=st.session_state.page_number == 0, key="first_page"
            ):
                st.session_state.page_number = 0
                st.rerun()

        with nav_col2:
            if st.button(
                "◀️", disabled=st.session_state.page_number == 0, key="prev_page"
            ):
                st.session_state.page_number -= 1
                st.rerun()

        with nav_col3:
            st.markdown(
                f"<div style='text-align: center; padding-top: 5px;'>{st.session_state.page_number + 1} / {total_pages}</div>",
                unsafe_allow_html=True,
            )

        with nav_col4:
            if st.button(
                "▶️",
                disabled=st.session_state.page_number >= total_pages - 1,
                key="next_page",
            ):
                st.session_state.page_number += 1
                st.rerun()

        with nav_col5:
            if st.button(
                "⏭️",
                disabled=st.session_state.page_number >= total_pages - 1,
                key="last_page",
            ):
                st.session_state.page_number = total_pages - 1
                st.rerun()

    # Display only the current page of decisions
    page_decisions = decisions[start_idx:end_idx]
    page_distances = distances[start_idx:end_idx] if distances else []

    for idx, decision in enumerate(page_decisions):
        distance = (
            page_distances[idx]
            if page_distances and idx < len(page_distances)
            else None
        )
        render_decision_card(decision, start_idx + idx, distance)

    # Pagination controls at bottom (simplified)
    if total_pages > 1:
        st.markdown("---")
        bottom_col1, bottom_col2, bottom_col3 = st.columns([1, 2, 1])
        with bottom_col2:
            page_jump = st.number_input(
                _("Go to page"),
                min_value=1,
                max_value=total_pages,
                value=st.session_state.page_number + 1,
                key="page_jump",
            )
            if page_jump != st.session_state.page_number + 1:
                st.session_state.page_number = page_jump - 1
                st.rerun()

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
