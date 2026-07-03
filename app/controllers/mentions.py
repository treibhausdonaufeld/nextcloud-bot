"""Mentions: who is mentioned where — table, network graph and details."""

import logging
from collections import defaultdict

from ravyn import JSONResponse, Request, Template, get

from app.i18n import template_context
from app.models import CollectivePage, Mention, NCUserList, PageSubtype
from app.settings import user_regex

logger = logging.getLogger(__name__)


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


def page_subtype_map(page_ids: set[int]) -> dict[int, CollectivePage]:
    pages = CollectivePage.fetch(limit=10000, page_id__in=list(page_ids))
    return {p.page_id: p for p in pages}


def mention_table_data() -> list[dict]:
    """Per-user mention statistics for all enabled users."""
    user_list = NCUserList()
    enabled = {u.username: u for u in user_list.get_enabled_users()}

    relations = Mention.all_user_page_relations()
    pages = page_subtype_map({r["page_id"] for r in relations})

    per_user: dict[str, list[dict]] = defaultdict(list)
    for r in relations:
        if r["username"] in enabled:
            per_user[r["username"]].append(r)

    table = []
    for username, rows in per_user.items():
        user = enabled[username]
        mentions = sum(r["mention_count"] for r in rows)
        distinct_pages = len({r["page_id"] for r in rows})

        protocol_count = 0
        groups: set[str] = set()
        for r in rows:
            page = pages.get(r["page_id"])
            if page and page.subtype == PageSubtype.PROTOCOL:
                protocol_count += 1
                if page.title and " " in page.title:
                    parts = page.title.split(" ", 1)
                    if len(parts) == 2:
                        groups.add(parts[1])

        table.append(
            {
                "displayname": user.displayname or username,
                "username": username,
                "mentions": mentions,
                "distinct_pages": distinct_pages,
                "distinct_protocols": protocol_count,
                "groups": ", ".join(sorted(groups)),
            }
        )

    table.sort(key=lambda row: row["mentions"], reverse=True)
    return table


def build_mention_graph(
    limit_user: str, limit_page_type: str
) -> tuple[list[dict], list[dict]]:
    user_list = NCUserList()
    enabled = {u.username: u for u in user_list.get_enabled_users()}

    relations = Mention.all_user_page_relations()
    pages = page_subtype_map({r["page_id"] for r in relations})

    filtered = [r for r in relations if r["username"] in enabled]
    if limit_user:
        filtered = [r for r in filtered if r["username"] == limit_user]
    if limit_page_type in ("protocol", "group"):
        filtered = [
            r
            for r in filtered
            if pages.get(r["page_id"])
            and pages[r["page_id"]].subtype == limit_page_type
        ]

    nodes: list[dict] = []
    edges: list[dict] = []
    added_users: set[str] = set()
    added_pages: set[int] = set()

    for r in filtered:
        username = r["username"]
        page_id = r["page_id"]
        page = pages.get(page_id)
        if not page:
            continue

        if username not in added_users:
            user = enabled[username]
            nodes.append(
                {
                    "id": f"user:{username}",
                    "label": user.displayname or username,
                    "size": 25,
                    "color": "#FF5733",  # Orange for users
                    "shape": "dot",
                    "title": username,
                }
            )
            added_users.add(username)

        if page_id not in added_pages:
            if page.subtype == PageSubtype.PROTOCOL:
                color = "#33C1FF"  # Blue for protocols
                label = f"📋 {page.title}"
            elif page.subtype == PageSubtype.GROUP:
                color = "#2FA24E"  # Green for group pages
                label = f"👥 {page.title}"
            else:
                color = "#DAA520"  # Gold for other pages
                label = f"📄 {page.title}"

            if len(label) > 30:
                label = label[:27] + "..."

            nodes.append(
                {
                    "id": f"page:{page_id}",
                    "label": label,
                    "size": 15,
                    "color": color,
                    "shape": "box",
                    "title": page.title,
                }
            )
            added_pages.add(page_id)

        edges.append({"from": f"user:{username}", "to": f"page:{page_id}"})

    return nodes, edges


@get("/mentions")
def mentions_page(
    request: Request,
    view: str = "table",
    limit_user: str = "",
    page_type: str = "",
    solver: str = "repulsion",
    height: int = 800,
    user: str = "",
) -> Template:
    user_list = NCUserList()
    table = mention_table_data()

    users = [
        {"username": u.username, "displayname": u.displayname or u.username}
        for u in sorted(
            user_list.get_enabled_users(), key=lambda u: u.displayname or ""
        )
    ]

    return Template(
        name="mentions.html",
        context=template_context(
            request,
            view=view if view in ("table", "graph") else "table",
            table=table,
            users=users,
            limit_user=limit_user,
            page_type=page_type,
            solver=solver,
            height=max(300, min(height, 1200)),
            selected_user=user,
        ),
    )


@get("/mentions/graph.json")
def mentions_graph(
    request: Request, limit_user: str = "", page_type: str = ""
) -> JSONResponse:
    nodes, edges = build_mention_graph(limit_user, page_type)
    return JSONResponse({"nodes": nodes, "edges": edges})


@get("/mentions/user/{username}")
def mention_user_detail(request: Request, username: str) -> Template:
    user_list = NCUserList()
    user = user_list.get_user_by_uid(username)

    rows = Mention.pages_for_user(username)
    pages = []
    for row in rows:
        page = CollectivePage.get_from_page_id_or_none(row["page_id"])
        if not page:
            continue
        pages.append(
            {
                "page": page,
                "subtype": page.subtype,
                "snippets": extract_mention_snippets(page.content or "", username),
            }
        )

    return Template(
        name="partials/mention_user_detail.html",
        context=template_context(
            request,
            username=username,
            displayname=(user.displayname if user else username) or username,
            pages=pages,
        ),
    )


@get("/mentions/page/{page_id}")
def mention_page_detail(request: Request, page_id: int) -> Template:
    user_list = NCUserList()
    page = CollectivePage.get_from_page_id_or_none(page_id)

    usernames = sorted(
        {m.username for m in Mention.fetch(limit=10000, page_id=page_id)}
    )
    display_names = []
    for username in usernames:
        user = user_list.get_user_by_uid(username)
        display_names.append((user.displayname if user else username) or username)

    return Template(
        name="partials/mention_page_detail.html",
        context=template_context(request, page=page, mentioned_users=display_names),
    )
