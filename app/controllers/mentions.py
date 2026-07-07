"""Mentions: who is mentioned where — per-user cards and details."""

import calendar
import logging
from collections import defaultdict
from datetime import datetime

from ravyn import Request, Template, get

from app.controllers.logbook import group_hue
from app.i18n import template_context
from app.models import CollectivePage, Mention, NCUserList, PageSubtype
from app.settings import user_regex

logger = logging.getLogger(__name__)

# Look-back durations offered by the filter dropdown; 0 means "all data".
MONTHS_OPTIONS = [1, 6, 12, 24, 0]
DEFAULT_MONTHS = 12


def months_ago_epoch(months: int, now: datetime | None = None) -> int:
    """Unix timestamp of `months` calendar months before `now`.

    Clamps the day for shorter target months (e.g. May 31 - 1 month -> Apr 30).
    """
    now = now or datetime.now()
    total = now.year * 12 + (now.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    day = min(now.day, calendar.monthrange(year, month)[1])
    return int(now.replace(year=year, month=month, day=day).timestamp())


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


def mention_card_data(months: int) -> list[dict]:
    """Per-user mention statistics for all enabled users.

    `months` limits counting to pages modified within the last N calendar
    months; 0 counts everything.
    """
    user_list = NCUserList()
    enabled = {u.username: u for u in user_list.get_enabled_users()}

    since = months_ago_epoch(months) if months > 0 else None
    relations = Mention.all_user_page_relations(since=since)
    pages = page_subtype_map({r["page_id"] for r in relations})

    per_user: dict[str, list[dict]] = defaultdict(list)
    for r in relations:
        if r["username"] in enabled:
            per_user[r["username"]].append(r)

    cards = []
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

        cards.append(
            {
                "displayname": user.displayname or username,
                "username": username,
                "mentions": mentions,
                "distinct_pages": distinct_pages,
                "distinct_protocols": protocol_count,
                "groups": [
                    {"name": name, "hue": group_hue(name)} for name in sorted(groups)
                ],
            }
        )

    cards.sort(key=lambda card: card["mentions"], reverse=True)
    return cards


@get("/mentions")
def mentions_page(
    request: Request,
    months: int = DEFAULT_MONTHS,
    user: str = "",
) -> Template:
    if months not in MONTHS_OPTIONS:
        months = DEFAULT_MONTHS

    return Template(
        name="mentions.html",
        context=template_context(
            request,
            cards=mention_card_data(months),
            months=months,
            months_options=MONTHS_OPTIONS,
            selected_user=user,
        ),
    )


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
