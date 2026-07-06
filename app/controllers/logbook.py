"""Logbook: browse and search decisions.

XLSX import is intentionally CLI-only (`cli.py import-xlsx`); there is no web
upload route.
"""

import hashlib
import logging
import re
from typing import Any

import markdown as markdown_lib
from markupsafe import Markup
from ravyn import Request, Template, get

from app.db import search
from app.i18n import template_context
from app.models import Decision, Group
from app.settings import _

logger = logging.getLogger(__name__)

ITEMS_PER_PAGE_OPTIONS = [10, 20, 50, 100]
DEFAULT_ITEMS_PER_PAGE = 20
PREVIEW_MAX_CHARS = 500

_MD_EXTENSIONS = ["extra", "nl2br", "sane_lists"]

_MD_FORMATTING_RE = re.compile(
    r"\[([^\]]+)\]\([^)]+\)"  # [text](url) -> text
    r"|~~(.+?)~~"  # ~~strikethrough~~ -> content
    r"|`([^`]+)`"  # `inline code` -> content
    r"|\*\*(.+?)\*\*"  # **bold** -> content
    r"|__(.+?)__"  # __bold__ -> content
    r"|\*(.+?)\*"  # *italic* -> content
    r"|_(.+?)_"  # _italic_ -> content
    r"|^#{1,6}\s*",  # heading markers -> remove
    re.MULTILINE,
)


def strip_markdown(text: str) -> str:
    """Remove markdown formatting characters from plain text for display."""
    if not text:
        return ""
    result = _MD_FORMATTING_RE.sub(
        lambda m: next(g for g in m.groups() if g is not None)
        if any(g is not None for g in m.groups())
        else "",
        text,
    )
    return result.strip()


def group_hue(name: str) -> int | None:
    """Map a group name to a stable hue (0-359) for card coloring.

    Uses an MD5 digest of the name so the colour is deterministic across
    processes (Python's built-in ``hash()`` is salted per interpreter run).
    Returns ``None`` for empty names so ungrouped cards stay neutral.
    """
    if not name:
        return None
    digest = hashlib.md5(name.encode("utf-8")).digest()
    return int.from_bytes(digest[:2], "big") % 360


def render_markdown(text: str | None) -> Markup:
    """Render decision markdown to HTML for display in the logbook cards."""
    if not text:
        return Markup("")
    # A fresh conversion per call keeps this safe under Ravyn's threadpool.
    return Markup(markdown_lib.markdown(text, extensions=_MD_EXTENSIONS))


def make_preview(text: str | None, limit: int = PREVIEW_MAX_CHARS) -> tuple[str, bool]:
    """Return a preview of at most ``limit`` chars and whether it was truncated.

    Truncation happens on a word boundary so the preview stays readable; the
    caller renders the full text behind an expand toggle.
    """
    stripped = (text or "").strip()
    if len(stripped) <= limit:
        return stripped, False
    truncated = stripped[:limit].rsplit(" ", 1)[0].rstrip()
    return truncated + "…", True


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
        preview_text, is_truncated = make_preview(decision.text)
        has_objections = bool((decision.objections or "").strip())
        has_more = is_truncated or bool(decision.context) or has_objections
        cards.append(
            {
                "decision": decision,
                "display_title": strip_markdown(decision.title) or _("No Title"),
                "preview_html": render_markdown(preview_text),
                "text_html": render_markdown(decision.text),
                "context_html": render_markdown(decision.context),
                "objections_html": render_markdown(decision.objections),
                "has_more": has_more,
                "has_objections": has_objections,
                "link": (d_page.url if d_page else "") or decision.external_link,
                "group_hue": group_hue(decision.group_name),
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
