"""Fetch pages from the Nextcloud Collectives app and store them in CouchDB.

This module uses the `settings.nextcloud` configuration (base_url,
admin_username, admin_password) and the existing `couchdb()` helper to upsert
documents into the CouchDB configured in `settings`.

Notes / assumptions:
- Tries a few likely Collectives API endpoints (v1 / v1.0). If the server
  exposes a different path, update ENDPOINTS accordingly.
- The code is defensive about the JSON shape returned (list vs dict with
  pages/data/items). If the list response doesn't include full page content
  a follow-up request to the page detail endpoint may be necessary (not
  implemented here until we know the server shape).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import requests
from pycouchdb.exceptions import NotFound

from lib.couchdb import couchdb
from lib.settings import settings

logger = logging.getLogger(__name__)


PAGES_LIST_ENDPOINT = (
    "/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collectives_id}/pages"
)
PAGE_DETAIL = "/remote.php/dav/files/{username}/{filepath}"


def _build_auth() -> tuple[str, str]:
    return (settings.nextcloud.admin_username, settings.nextcloud.admin_password)


def _try_fetch_from_endpoint(url: str) -> List[Dict[str, Any]] | None:
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

    return data["ocs"]["data"]["pages"]


def fetch_all_pages() -> List[Dict[str, Any]]:
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


def fetch_page_markdown(page: dict) -> str:
    """Fetch the markdown content of a collectives page via WebDAV."""
    base = settings.nextcloud.base_url

    # settings.nextcloud.base_url is a pydantic HttpUrl — convert to str
    base_str = str(base).rstrip("/")

    slug = page.get("slug") or page.get("id")
    if not slug:
        raise ValueError("Page does not have a slug or id for URL construction")

    filepath = "/".join((page["collectivePath"], page["filePath"], page["fileName"]))

    url = base_str + PAGE_DETAIL.format(
        username=settings.nextcloud.admin_username, filepath=filepath
    )

    auth = _build_auth()
    logger.debug("Fetching markdown content for page %s from %s", slug, url)
    resp = requests.get(url, auth=auth, headers={"Accept": "text/markdown"}, timeout=90)
    resp.raise_for_status()

    return resp.text


def _make_doc_for_page(page: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a page dict from the API into a CouchDB document."""
    # Best-effort id extraction from common keys
    page_id = page.get("id")

    doc_id = (
        f"collective:{settings.nextcloud.collectives_id}:{page_id}" if page_id else None
    )

    doc: Dict[str, Any] = {
        "type": "collective_page",
        "title": page.get("title") or page.get("name"),
        "emoji": page.get("emoji"),
        "content": fetch_page_markdown(page),
        "raw": page,
    }

    if doc_id:
        doc["_id"] = doc_id

    # optional fields
    for k in ("modifiedAt", "modified_at", "updated_at", "mtime"):
        if k in page:
            doc["modified_at"] = page[k]
            break

    for k in ("createdAt", "created_at", "ctime", "created"):
        if k in page:
            doc["created_at"] = page[k]
            break

    # build a best-effort URL to the page
    slug = page.get("slug") or page.get("id")
    if slug:
        doc["url"] = (
            f"{str(settings.nextcloud.base_url).rstrip('/')}/apps/collectives/page/{slug}"
        )

    return doc


def store_pages_to_couchdb(pages: List[Dict[str, Any]]) -> int:
    """Upsert the given pages into CouchDB. Returns number of stored docs."""
    db = couchdb()
    stored = 0
    for page in pages:
        doc = _make_doc_for_page(page)
        if not doc.get("_id"):
            logger.warning("Skipping page without identifiable id: %s", page)
            continue

        doc_id = doc["_id"]
        # try to fetch existing doc to obtain _rev for update
        try:
            existing = db.get(doc_id)
            if existing and isinstance(existing, dict):
                doc["_rev"] = existing.get("_rev")
        except NotFound:
            pass

        try:
            db.save(doc)
            stored += 1
            logger.info("Stored collectives page to CouchDB: %s", doc_id)
        except Exception as e:
            logger.exception("Failed to save page %s: %s", doc_id, e)

    return stored


def fetch_and_store_all_pages() -> int:
    """Convenience function: fetch pages and store them into CouchDB.

    Returns the number of pages stored.
    """
    pages = fetch_all_pages()
    return store_pages_to_couchdb(pages)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        n = fetch_and_store_all_pages()
        print(f"Stored {n} collectives pages into CouchDB")
    except Exception as e:
        logger.exception("Error fetching/storing collectives pages: %s", e)
        raise
