"""Fetch pages from the Nextcloud Collectives app and store them in the database.

This module uses the `settings.nextcloud` configuration (base_url,
admin_username, admin_password) to fetch page metadata via the OCS API and
raw markdown via WebDAV, upserting `CollectivePage` rows.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import List, Set

import requests

from app.models.collective_page import CollectivePage, OCSCollectivePage, PageSubtype
from app.models.protocol_version import ProtocolVersion
from app.settings import settings

logger = logging.getLogger(__name__)


PAGES_LIST_ENDPOINT = (
    "/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collectives_id}/pages"
)
PAGE_CONTENT_URL = "/remote.php/dav/files/{username}/{filepath}"


def _build_auth() -> tuple[str, str]:
    return (settings.nextcloud.admin_username, settings.nextcloud.admin_password)


def _try_fetch_from_endpoint(url: str) -> List[OCSCollectivePage] | None:
    """Try to GET the given URL and return a list of page dicts if found.

    Returns None when the endpoint did not return a usable list.
    """
    auth = _build_auth()
    logger.debug("Trying to fetch collectives pages from %s", url)
    resp = requests.get(
        url,
        auth=auth,
        headers={"Accept": "application/json", "OCS-APIRequest": "true"},
        timeout=90,
    )
    resp.raise_for_status()

    data = resp.json()

    # Nextcloud OCS responses nest the result under ocs->data->pages
    pages = data.get("ocs", {}).get("data", {}).get("pages")
    if not pages:
        return None

    parsed: List[OCSCollectivePage] = []
    for p in pages:
        parsed.append(OCSCollectivePage(**p))

    return parsed


def fetch_ocs_collective_page(page_id: int) -> OCSCollectivePage:
    """Fetch a single collectives page by its OCS id."""
    base = settings.nextcloud.base_url
    if not base:
        raise RuntimeError("settings.nextcloud.base_url is not configured")

    # settings.nextcloud.base_url is a pydantic HttpUrl — convert to str
    base_str = str(base).rstrip("/")

    url = (
        base_str
        + PAGES_LIST_ENDPOINT.format(collectives_id=settings.nextcloud.collectives_id)
        + f"/{page_id}"
    )

    auth = _build_auth()
    logger.debug("Fetching collectives page %d from %s", page_id, url)
    resp = requests.get(
        url,
        auth=auth,
        headers={"Accept": "application/json", "OCS-APIRequest": "true"},
        timeout=90,
    )
    resp.raise_for_status()

    data = resp.json()
    page_data = data.get("ocs", {}).get("data", {}).get("page", {})
    if not page_data:
        raise RuntimeError(f"Page data for id {page_id} not found in response")

    page = OCSCollectivePage(**page_data)
    page.content = fetch_page_markdown(page)

    return page


def fetch_all_pages() -> List[OCSCollectivePage]:
    """Fetch all pages from Nextcloud Collectives.

    Raises RuntimeError when no endpoint returns a usable result.
    """
    base = settings.nextcloud.base_url
    if not base:
        raise RuntimeError("settings.nextcloud.base_url is not configured")

    # settings.nextcloud.base_url is a pydantic HttpUrl — convert to str
    base_str = str(base).rstrip("/")

    url = base_str + PAGES_LIST_ENDPOINT.format(
        collectives_id=settings.nextcloud.collectives_id
    )
    pages = _try_fetch_from_endpoint(url)
    if pages is not None:
        logger.info("Fetched %d pages from %s", len(pages), url)
        return pages

    raise RuntimeError("Unable to fetch collectives pages from Nextcloud")


def fetch_page_markdown(page: OCSCollectivePage) -> str:
    """Fetch the markdown content of a collectives page via WebDAV."""
    base = settings.nextcloud.base_url

    # settings.nextcloud.base_url is a pydantic HttpUrl — convert to str
    base_str = str(base).rstrip("/")

    slug = page.slug or str(page.id)
    if not slug:
        raise ValueError("Page does not have a slug or id for URL construction")

    filepath = "/".join(
        (page.collectivePath or "", page.filePath or "", page.fileName or "")
    )

    url = base_str + PAGE_CONTENT_URL.format(
        username=settings.nextcloud.admin_username, filepath=filepath
    )

    auth = _build_auth()
    logger.debug("Fetching markdown content for page %s from %s", slug, url)
    resp = requests.get(url, auth=auth, headers={"Accept": "text/markdown"}, timeout=90)
    resp.raise_for_status()

    return resp.text


def is_protocol_page_safe(page: CollectivePage) -> bool:
    """Check whether a page is a protocol page without requiring the config.

    `Protocol.is_protocol_page` needs the bot config from Nextcloud; when it
    is unavailable, fall back to the subtype stored by the parser.
    """
    if page.subtype == PageSubtype.PROTOCOL:
        return True
    try:
        from app.models.protocol import Protocol

        return Protocol.is_protocol_page(page)
    except Exception:
        return False


def snapshot_protocol_page(page: CollectivePage) -> None:
    """Record a protocol version and copy its embedded media.

    Both operations are idempotent; failures must never break the sync.
    """
    from app.services.protocol_media import sync_page_media

    try:
        ProtocolVersion.record(page)
        sync_page_media(page)
    except Exception:
        logger.exception("Failed to snapshot protocol page %s", page.page_id)


def store_pages(pages: List[OCSCollectivePage]) -> List[CollectivePage]:
    """Upsert the given pages into the database. Returns the stored pages."""
    stored = []

    for page in pages:
        doc = CollectivePage.get_from_page_id_or_none(page_id=page.id)
        if doc is not None:
            if doc.updated_at and page.timestamp and page.timestamp < doc.updated_at:
                logger.debug("Page %s unchanged, skipping", doc.title)
                continue
        else:
            doc = CollectivePage(page_id=page.id)

        try:
            doc.apply_ocs(page)
            doc.content = fetch_page_markdown(page)
            doc.store()
            if is_protocol_page_safe(doc):
                snapshot_protocol_page(doc)
            stored.append(doc)
            logger.info("Stored collectives page: %s, %s", doc.title, doc.page_id)
        except Exception as e:
            logger.exception("Failed to save page %s: %s", doc.title, e)

    return stored


# Protocols older than this are never deleted from the database, even when
# they disappear from Nextcloud — their history stays self-contained here.
PROTOCOL_DELETE_PROTECTION_DAYS = 7


def protocol_age_days(page: CollectivePage) -> int | None:
    """Age of a protocol in days, or None when it cannot be determined.

    Prefers the parsed protocol date; falls back to the page's Nextcloud
    modification timestamp.
    """
    from app.models.protocol import Protocol

    protocol = Protocol.fetch_one(page_id=page.page_id)
    if protocol is not None:
        try:
            date_obj = protocol.date_obj
        except ValueError:
            date_obj = None
        if date_obj is not None:
            return (datetime.now().date() - date_obj).days

    if page.timestamp:
        try:
            return int((time.time() - float(page.timestamp)) // 86400)
        except (TypeError, ValueError):
            return None
    return None


def delete_orphaned_pages(fetched_page_ids: Set[int]) -> None:
    """Delete pages from the database that are no longer in Nextcloud.

    Protocol pages older than PROTOCOL_DELETE_PROTECTION_DAYS are never
    deleted (renaming a page keeps its page_id, so renames are unaffected).
    Each page's remove method handles cleanup of related objects and the
    search index.

    Args:
        fetched_page_ids: Set of page IDs currently in Nextcloud
    """
    stored_pages = CollectivePage.fetch(limit=10000)

    for page in stored_pages:
        if page.page_id in fetched_page_ids:
            continue

        if is_protocol_page_safe(page):
            age = protocol_age_days(page)
            # When the age is unknown, err on the side of keeping the protocol.
            if age is None or age > PROTOCOL_DELETE_PROTECTION_DAYS:
                logger.info(
                    "Keeping orphaned protocol page: %s (page_id=%s, age=%s days)",
                    page.title,
                    page.page_id,
                    age,
                )
                continue

        logger.info("Deleting orphaned page: %s (page_id=%s)", page.title, page.page_id)
        page.remove()


def fetch_and_store_all_pages() -> List[CollectivePage]:
    """Convenience function: fetch pages and store them into the database.

    Also removes orphaned pages and their related objects.

    Returns the list of pages stored.
    """
    pages = fetch_all_pages()
    stored = store_pages(pages)

    # Clean up pages that were deleted from Nextcloud
    fetched_page_ids = {p.id for p in pages}
    delete_orphaned_pages(fetched_page_ids)

    return stored
