"""Fetch pages from the Nextcloud Collectives app and store them in the database.

This module uses the `settings.nextcloud` configuration (base_url,
admin_username, admin_password) to fetch page metadata via the OCS API and
raw markdown via WebDAV, upserting `CollectivePage` rows.
"""

from __future__ import annotations

import logging
from typing import List, Set

import requests

from app.models.collective_page import CollectivePage, OCSCollectivePage
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
            stored.append(doc)
            logger.info("Stored collectives page: %s, %s", doc.title, doc.page_id)
        except Exception as e:
            logger.exception("Failed to save page %s: %s", doc.title, e)

    return stored


def delete_orphaned_pages(fetched_page_ids: Set[int]) -> None:
    """Delete pages from the database that are no longer in Nextcloud.

    Each page's remove method handles cleanup of related objects and the
    search index.

    Args:
        fetched_page_ids: Set of page IDs currently in Nextcloud
    """
    stored_pages = CollectivePage.fetch(limit=10000)

    for page in stored_pages:
        if page.page_id not in fetched_page_ids:
            logger.info(
                "Deleting orphaned page: %s (page_id=%s)", page.title, page.page_id
            )
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
