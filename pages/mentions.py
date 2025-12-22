import pandas as pd
import streamlit as st
from streamlit_agraph import Config, Edge, Node, agraph

from lib.couchdb import couchdb
from lib.menu import menu
from lib.nextcloud.models.collective_page import CollectivePage, PageSubtype
from lib.nextcloud.models.user import NCUser, NCUserList
from lib.settings import _, settings, user_regex
from lib.streamlit_oauth import load_user_data

node_label_font = "#E0E0E0" if st.context.theme.type == "dark" else "#2C2C2C"


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


@st.cache_data(ttl=3600)
def get_all_user_page_relations(
    _users: list[NCUser],
) -> tuple[dict[str, dict], dict[int, CollectivePage], list[tuple[str, int]]]:
    """Get all user-page relations for the graph. Returns users dict, pages dict, and edges list."""
    db = couchdb()
    users_with_mentions: dict[str, dict] = {}
    all_pages: dict[int, CollectivePage] = {}
    edges: list[tuple[str, int]] = []  # (username, page_id)

    for user in _users:
        try:
            user_view_result = db.query(
                "mentions/by_user", key=user.username, reduce=False, include_docs=True
            )
            rows = list(user_view_result)

            if len(rows) > 0:
                users_with_mentions[user.username] = {
                    "displayname": user.ocs.displayname or user.username,
                    "username": user.username,
                }

                for row in rows:
                    try:
                        if "doc" in row:
                            page = CollectivePage(**row["doc"])
                            if page.ocs and page.ocs.id:
                                all_pages[page.ocs.id] = page
                                edges.append((user.username, page.ocs.id))
                    except Exception:
                        continue
        except Exception:
            continue

    return users_with_mentions, all_pages, edges


def build_mention_graph(
    users_with_mentions: dict[str, dict],
    all_pages: dict[int, CollectivePage],
    edges: list[tuple[str, int]],
    user_list: NCUserList,
    limit_user: str | None = None,
    limit_page_type: str | None = None,
) -> tuple[list[Node], list[Edge]]:
    """Build nodes and edges for the mention graph."""
    nodes: list[Node] = []
    graph_edges: list[Edge] = []

    # Track which nodes we've added
    added_users: set[str] = set()
    added_pages: set[int] = set()

    # Filter edges based on limits
    filtered_edges = edges
    if limit_user:
        filtered_edges = [(u, p) for u, p in filtered_edges if u == limit_user]

    if limit_page_type:
        if limit_page_type == "protocol":
            filtered_edges = [
                (u, p)
                for u, p in filtered_edges
                if p in all_pages and all_pages[p].subtype == PageSubtype.PROTOCOL
            ]
        elif limit_page_type == "group":
            filtered_edges = [
                (u, p)
                for u, p in filtered_edges
                if p in all_pages and all_pages[p].subtype == PageSubtype.GROUP
            ]

    # Build nodes from filtered edges
    for username, page_id in filtered_edges:
        # Add user node if not already added
        if username not in added_users and username in users_with_mentions:
            user_info = users_with_mentions[username]
            nodes.append(
                Node(
                    id=f"user:{username}",
                    label=str(user_info["displayname"]),
                    size=25,
                    color="#FF5733",  # Orange for users
                    shape="dot",
                    title=username,
                    font={"color": node_label_font},
                )
            )
            added_users.add(username)

        # Add page node if not already added
        if page_id not in added_pages and page_id in all_pages:
            page = all_pages[page_id]
            # Determine page color and icon based on type
            if page.subtype == PageSubtype.PROTOCOL:
                color = "#33C1FF"  # Blue for protocols
                label = f"📋 {page.title}"
            elif page.subtype == PageSubtype.GROUP:
                color = "#2FA24E"  # Green for group pages
                label = f"👥 {page.title}"
            else:
                color = "#DAA520"  # Gold for other pages
                label = f"📄 {page.title}"

            # Truncate label if too long
            if len(label) > 30:
                label = label[:27] + "..."

            nodes.append(
                Node(
                    id=f"page:{page_id}",
                    label=label,
                    size=15,
                    color=color,
                    shape="box",
                    title=page.title,
                    font={"color": node_label_font},
                )
            )
            added_pages.add(page_id)

        # Add edge
        if username in added_users and page_id in added_pages:
            graph_edges.append(
                Edge(
                    source=f"user:{username}",
                    target=f"page:{page_id}",
                    type="CURVE_SMOOTH",
                )
            )

    return nodes, graph_edges


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
    # View selector
    view_mode = st.radio(
        _("View Mode"),
        options=["graph", "table"],
        format_func=lambda x: _("Table View") if x == "table" else _("Graph View"),
        horizontal=True,
    )

    if view_mode == "graph":
        # Graph visualization
        st.markdown("### " + _("Mention Network"))
        st.caption(
            _(
                "🔴 Users | 📋 Protocols (blue) | 👥 Group Pages (green) | 📄 Other Pages (gold)"
            )
        )

        # Graph controls
        cols = st.columns(4)
        limit_user_graph = cols[0].selectbox(
            _("Filter by User"),
            options=[""] + sorted([uid for uid in user_list.users.keys()]),
            format_func=lambda uid: str(user_list[uid].ocs.displayname)
            if uid
            else _("All Users"),
            key="graph_user_filter",
        )
        limit_page_type = cols[1].selectbox(
            _("Filter by Page Type"),
            options=["", "protocol", "group"],
            format_func=lambda x: {
                "": _("All Pages"),
                "protocol": _("Protocols Only"),
                "group": _("Group Pages Only"),
            }.get(x, x),
        )
        solver = cols[2].selectbox(
            _("Solver"),
            options=[
                "repulsion",
                "forceAtlas2Based",
            ],
            index=0,
        )
        graph_height = cols[3].slider(_("Graph Height"), 300, 1200, 800, 100)

        # Get all user-page relations
        users_with_mentions, all_pages, edges = get_all_user_page_relations(users)

        # Build graph
        nodes, graph_edges = build_mention_graph(
            users_with_mentions,
            all_pages,
            edges,
            user_list,
            limit_user=limit_user_graph if limit_user_graph else None,
            limit_page_type=limit_page_type if limit_page_type else None,
        )

        if nodes:
            config = Config(
                width=1600,
                height=graph_height,
                directed=False,
                nodeHighlightBehavior=True,
                highlightColor="#BE3230",
                hierarchical=False,
                physics=True,
                collapsible=True,
                solver=solver,
                stabilization=True,
                node={"labelProperty": "label", "fontColor": "#F0F0F0"},
                link={"labelProperty": "label", "renderLabel": False},
            )

            selected_node = agraph(nodes=nodes, edges=graph_edges, config=config)

            # Show details for selected node
            if selected_node:
                if selected_node.startswith("user:"):
                    username = selected_node.split(":", 1)[1]
                    user = user_list[username]
                    st.markdown(f"### {user.ocs.displayname}")

                    # Get pages for this user
                    pages_list = get_user_mentions(username)
                    st.write(
                        _("Total pages mentioning user: {count}").format(
                            count=len(pages_list)
                        )
                    )

                    for page in pages_list[:10]:  # Show first 10
                        if page.subtype == PageSubtype.PROTOCOL:
                            icon = "📋"
                        elif page.subtype == PageSubtype.GROUP:
                            icon = "👥"
                        else:
                            icon = "📄"
                        st.write(f"- {icon} [{page.title}]({page.url})")

                    if len(pages_list) > 10:
                        st.caption(
                            _("... and {count} more pages").format(
                                count=len(pages_list) - 10
                            )
                        )

                elif selected_node.startswith("page:"):
                    page_id = int(selected_node.split(":", 1)[1])
                    if page_id in all_pages:
                        page = all_pages[page_id]
                        st.markdown(f"### {page.title}")
                        if page.url:
                            st.markdown(f"[{_('Open page')}]({page.url})")

                        # Find all users mentioned in this page
                        mentioned_users = [u for u, p in edges if p == page_id]
                        mentioned_users = list(set(mentioned_users))  # Deduplicate
                        st.write(
                            _("Users mentioned in this page: {count}").format(
                                count=len(mentioned_users)
                            )
                        )
                        for username in sorted(mentioned_users):
                            user = user_list[username]
                            st.write(f"- {user.ocs.displayname}")
        else:
            st.info(_("No data to display with current filters."))

    else:
        # Table view (original functionality)
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
        df_sorted = df.sort_values(by=_("Mentions"), ascending=False).reset_index(
            drop=True
        )

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
