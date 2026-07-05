"""Dashboard: full-text search over pages and decisions."""

import html
import logging

from ravyn import Request, Template, get

from app.db import search
from app.i18n import template_context
from app.models import CollectivePage, Decision

logger = logging.getLogger(__name__)


def _safe_snippet(snippet: str) -> str:
    """Escape a FTS snippet but keep the <mark> highlighting tags."""
    escaped = html.escape(snippet)
    return escaped.replace("&lt;mark&gt;", "<mark>").replace("&lt;/mark&gt;", "</mark>")


def build_search_results(query: str, doc_type: str, limit: int) -> list[dict]:
    doc_types = [doc_type] if doc_type in ("page", "decision") else None
    results = []
    for row in search(query, doc_types=doc_types, limit=limit):
        item = {
            "doc_type": row["doc_type"],
            "title": row["title"],
            "snippet": _safe_snippet(row["snippet"]),
            "url": "",
            "date": "",
        }
        if row["doc_type"] == "page":
            page = CollectivePage.get_from_page_id_or_none(int(row["doc_id"]))
            if page:
                item["url"] = page.url or ""
                item["date"] = page.formatted_timestamp or ""
        else:
            decision = Decision.fetch_one(id=int(row["doc_id"]))
            if decision:
                item["date"] = decision.date
                page = decision.page
                item["url"] = (page.url if page else "") or decision.external_link
        results.append(item)
    return results


@get("/")
def dashboard(request: Request) -> Template:
    return Template(name="dashboard.html", context=template_context(request))


@get("/search")
def search_results(
    request: Request, q: str = "", doc_type: str = "", limit: int = 25
) -> Template:
    limit = max(1, min(limit, 100))
    results = build_search_results(q, doc_type, limit) if q.strip() else []
    return Template(
        name="partials/search_results.html",
        context=template_context(request, results=results, query=q),
    )
