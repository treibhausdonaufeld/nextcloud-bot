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
import re
from typing import List, cast

import requests
from pycouchdb.exceptions import NotFound

from lib.nextcloud.models import CollectivePage, OCSCollectivePage, PageSubtype
from lib.settings import settings, user_regex

logger = logging.getLogger(__name__)


PAGES_LIST_ENDPOINT = (
    "/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collectives_id}/pages"
)
PAGE_DETAIL = "/remote.php/dav/files/{username}/{filepath}"


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

    url = base_str + PAGE_DETAIL.format(
        username=settings.nextcloud.admin_username, filepath=filepath
    )

    auth = _build_auth()
    logger.debug("Fetching markdown content for page %s from %s", slug, url)
    resp = requests.get(url, auth=auth, headers={"Accept": "text/markdown"}, timeout=90)
    resp.raise_for_status()

    return resp.text


def store_pages_to_couchdb(pages: List[OCSCollectivePage]) -> int:
    """Upsert the given pages into CouchDB. Returns number of stored docs."""
    stored = 0
    for page in pages:
        # _make_doc_for_page returns a dict suitable for CouchDB
        doc = CollectivePage.from_ocs_page(page=page)
        if not doc.id:
            logger.warning("Skipping page without identifiable id: %s", page)
            continue

        # try to fetch existing doc to obtain _rev for update
        try:
            doc.load()
            # existing timestamp is stored at top-level in the doc
            if doc.updated_at and page.timestamp and page.timestamp < doc.updated_at:
                logger.info("Page %s unchanged, skipping", doc.title)
                continue
        except NotFound:
            pass

        try:
            doc.content = fetch_page_markdown(page)
            doc.save()
            stored += 1
            logger.info("Stored collectives page to CouchDB: %s", doc.title)
        except Exception as e:
            logger.exception("Failed to save page %s: %s", doc.title, e)

    return stored


def fetch_and_store_all_pages() -> int:
    """Convenience function: fetch pages and store them into CouchDB.

    Returns the number of pages stored.
    """
    pages = fetch_all_pages()
    return store_pages_to_couchdb(pages)


def parse_content(page: CollectivePage) -> None:
    """Parse metadata from the markdown content."""
    from lib.nextcloud.config import bot_config

    if not page.content or not page.content:
        return

    if not page.subtype:
        if page.title.upper().startswith(tuple(bot_config.organisation.group_prefixes)):
            page.subtype = PageSubtype.GROUP
        elif any(
            kw in page.ocs.filePath.lower()
            for kw in bot_config.organisation.protocol_subtype_keywords
        ):
            page.subtype = PageSubtype.PROTOCOL
            page.date = page.ocs.title.split(" ")[0]  # first word as date

    lines = page.content.splitlines()

    first_word_regex = re.compile(r"\b(\w[\w-]*)\b")

    for line in lines:
        # get the first word on the line, ignoring any leading non-word chars
        m = first_word_regex.search(line)
        if not m:
            continue
        first_word = m.group(1).lower()

        users = re.findall(user_regex, line)

        if first_word in bot_config.organisation.moderation_person_keywords:
            page.moderated_by = ", ".join(users)
        elif first_word in bot_config.organisation.protocol_person_keywords:
            page.protocol_by = ", ".join(users)
        elif first_word in bot_config.organisation.participant_keywords:
            page.participants.extend(users)


def parse_pages() -> None:
    """Fetch all pages from CouchDB and parse their content."""

    for page in cast(List[CollectivePage], CollectivePage.load_all(limit=500)):
        parse_content(page)
        page.save()
        logger.info("Parsed content for page %s", page.id)
