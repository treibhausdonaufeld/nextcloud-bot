import pandas as pd
import streamlit as st

from lib.couchdb import couchdb
from lib.menu import menu
from lib.nextcloud.models.collective_page import CollectivePage, PageSubtype
from lib.nextcloud.models.user import NCUser, NCUserList
from lib.settings import _, settings, user_regex
from lib.streamlit_oauth import load_user_data


def extract_mention_snippets(
    content: str, username: str, context_chars: int = 500
) -> list[str]:
    """Extract snippets around mentions of a user in content."""
    if not content:
        return []

    snippets = []
    # Find all mentions of this user
    for match in user_regex.finditer(content):
        if match.group(1) == username:
            start = max(0, match.start() - context_chars)
            end = min(len(content), match.end() + context_chars)
            snippet = content[start:end]
            # Clean up the snippet
            if start > 0:
                snippet = "..." + snippet
            if end < len(content):
                snippet = snippet + "..."
            snippets.append(snippet.replace("\n", " ").strip())
    return snippets


@st.cache_data(ttl=3600)
def get_mention_counts(
    _users: list[NCUser],
) -> list[dict[str, str | int]]:
    """Calculate mention counts for all users. Cached for 1 hour."""
    db = couchdb()
    mention_counts: list[dict[str, str | int]] = []

    for user in _users:
        try:
            user_view_result = db.query(
                "mentions/by_user", key=user.username, reduce=False, include_docs=True
            )
            rows = list(user_view_result)
            count = len(rows)

            if count > 0:
                pages: dict[int, CollectivePage] = {}
                for row in rows:
                    try:
                        if "doc" in row:
                            page = CollectivePage(**row["doc"])
                            if page.ocs and page.ocs.id:
                                pages[page.ocs.id] = page
                    except Exception:
                        continue

                distinct_pages_count = len(pages)
                protocol_count = 0
                groups: set[str] = set()

                for page in pages.values():
                    if page.subtype == PageSubtype.PROTOCOL:
                        protocol_count += 1
                        if page.title and " " in page.title:
                            parts = page.title.split(" ", 1)
                            if len(parts) == 2:
                                groups.add(parts[1])

                mention_counts.append(
                    {
                        "displayname": user.ocs.displayname or user.username,
                        "username": user.username,
                        "mentions": count,
                        "distinct_pages": distinct_pages_count,
                        "distinct_protocols": protocol_count,
                        "groups": ", ".join(sorted(groups)),
                    }
                )
        except Exception:
            continue

    return mention_counts


@st.cache_data(ttl=3600)
def get_user_mentions(username: str) -> list[CollectivePage]:
    """Get all pages where a user is mentioned. Cached for 1 hour."""
    db = couchdb()
    user_view_result = db.query(
        "mentions/by_user", key=username, reduce=False, include_docs=True
    )

    pages_with_mentions: dict[int, CollectivePage] = {}
    for row in user_view_result:
        try:
            if "doc" in row:
                page = CollectivePage(**row["doc"])
                if page.ocs and page.ocs.id:
                    pages_with_mentions[page.ocs.id] = page
        except Exception:
            continue

    return sorted(
        pages_with_mentions.values(),
        key=lambda p: p.ocs.timestamp or 0,
        reverse=True,
    )


# Streamlit app starts here
title = _("Mentions").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="📣", layout="wide")

menu()
load_user_data()

user_list = NCUserList()

st.title(title)

# Get cached mention counts
users = list(user_list.users.values())
mention_counts = get_mention_counts(users)

if mention_counts:
    # Build dataframe with translated column names
    df = pd.DataFrame(
        [
            {
                _("User"): row["displayname"],
                "username": row["username"],
                _("Mentions"): row["mentions"],
                _("Distinct Pages"): row["distinct_pages"],
                _("Distinct Protocols"): row["distinct_protocols"],
                _("Groups"): row["groups"],
            }
            for row in mention_counts
        ]
    )
    df_sorted = df.sort_values(by=_("Mentions"), ascending=False).reset_index(drop=True)

    # User selection
    user_options = [""] + df_sorted[_("User")].tolist()
    selected_user = st.selectbox(
        _("Select user for details"),
        options=user_options,
        format_func=lambda x: _("Select a user...") if x == "" else x,
    )

    # Display columns (exclude internal username column)
    display_cols = [c for c in df_sorted.columns if c != "username"]

    # Bold the selected user row
    def bold_selected_row(row: pd.Series) -> list[str]:
        if selected_user and row[_("User")] == selected_user:
            return ["font-weight: bold"] * len(row)
        return [""] * len(row)

    styled_df = df_sorted[display_cols].style.apply(bold_selected_row, axis=1)
    st.dataframe(styled_df, hide_index=True, width="stretch")

    # Show details for selected user
    if selected_user:
        # Find username from displayname
        selected_row = df_sorted[df_sorted[_("User")] == selected_user].iloc[0]
        username = selected_row["username"]

        st.markdown(f"### {_('Mention Details for')} **{selected_user}**")

        # Get cached user mentions
        pages_list = get_user_mentions(username)

        # Display each page with mentions
        for page in pages_list:
            # Determine page type
            if page.subtype == PageSubtype.PROTOCOL:
                page_type = _("Protocol")
                icon = "📋"
            elif page.subtype == PageSubtype.GROUP:
                page_type = _("Group Page")
                icon = "👥"
            else:
                page_type = _("Page")
                icon = "📄"

            # Create expander for each page
            with st.expander(
                f"{icon} **{page.title}** ({page_type}) - {page.formatted_timestamp or ''}",
                expanded=False,
            ):
                col1, col2 = st.columns([3, 1])
                with col1:
                    if page.url:
                        st.markdown(f"[{_('Open page')}]({page.url})")
                with col2:
                    st.caption(f"{_('Type')}: {page_type}")

                # Extract and display snippets
                snippets = extract_mention_snippets(page.content or "", username)
                if snippets:
                    st.markdown(f"**{_('Mentions')}:**")
                    for snippet in snippets:
                        st.markdown(f"> {snippet}")
                else:
                    st.caption(_("No snippet available"))
else:
    st.info(_("No mentions found."))
