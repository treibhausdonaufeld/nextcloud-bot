"""Logbook: browse and search decisions.

XLSX import is intentionally CLI-only (`cli.py import-xlsx`); there is no web
upload route.
"""

import logging
from typing import Any

from ravyn import Request, Template, get

from app.db import search
from app.i18n import template_context
from app.models import Decision, Group

logger = logging.getLogger(__name__)

ITEMS_PER_PAGE_OPTIONS = [10, 20, 50, 100]
DEFAULT_ITEMS_PER_PAGE = 20


def truncate_text(text: str | None, max_length: int = 300) -> str:
    """Truncate text to max_length characters with ellipsis."""
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[:max_length].rsplit(" ", 1)[0] + "..."


def matches_search(decision: Decision, search_text: str, search_type: str) -> bool:
    """Check if a decision matches the search criteria in title, text, or objections."""
    searchable_text = " ".join(
        [decision.title or "", decision.text or "", decision.objections or ""]
    ).lower()

    search_lower = search_text.lower()

    if search_type == "exact":
        return search_lower in searchable_text
    elif search_type == "all":
        words = search_lower.split()
        return all(word in searchable_text for word in words)
    elif search_type == "any":
        words = search_lower.split()
        return any(word in searchable_text for word in words)
    return False


def find_decisions(group: str, q: str, search_type: str) -> list[Decision]:
    filters: dict[str, Any] = {"group_name": group} if group else {}

    if not q.strip():
        decisions = Decision.fetch(limit=10000, **filters)
        return sorted(decisions, key=lambda d: d.date, reverse=True)

    if search_type == "fulltext":
        hits = search(q, doc_types=["decision"], limit=100)
        decisions = []
        for row in hits:
            decision = Decision.fetch_one(id=int(row["doc_id"]))
            if decision and (not group or decision.group_name == group):
                decisions.append(decision)
        return decisions

    all_decisions = Decision.fetch(limit=10000, **filters)
    return sorted(
        [d for d in all_decisions if matches_search(d, q, search_type)],
        key=lambda d: d.date,
        reverse=True,
    )


def logbook_context(
    request: Request,
    group: str,
    q: str,
    search_type: str,
    page: int,
    per_page: int,
) -> dict:
    decisions = find_decisions(group, q, search_type)

    if per_page not in ITEMS_PER_PAGE_OPTIONS:
        per_page = DEFAULT_ITEMS_PER_PAGE
    total = len(decisions)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * per_page
    end_idx = min(start_idx + per_page, total)

    cards: list[dict[str, Any]] = []
    for decision in decisions[start_idx:end_idx]:
        d_page = decision.page
        cards.append(
            {
                "decision": decision,
                "truncated": truncate_text(decision.text, 300),
                "link": (d_page.url if d_page else "") or decision.external_link,
            }
        )

    group_names = sorted(g.name for g in Group.fetch(limit=1000))

    return template_context(
        request,
        cards=cards,
        total=total,
        total_pages=total_pages,
        page=page,
        per_page=per_page,
        per_page_options=ITEMS_PER_PAGE_OPTIONS,
        start_idx=start_idx,
        end_idx=end_idx,
        group_names=group_names,
        selected_group=group,
        q=q,
        search_type=search_type,
    )


@get("/logbook")
def logbook_page(
    request: Request,
    group: str = "",
    q: str = "",
    search_type: str = "fulltext",
    page: int = 1,
    per_page: int = DEFAULT_ITEMS_PER_PAGE,
) -> Template:
    return Template(
        name="logbook.html",
        context=logbook_context(request, group, q, search_type, page, per_page),
    )
