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
from typing import List, cast

from pycouchdb.exceptions import NotFound

from lib.nextcloud.config import BotConfig, bot_config
from lib.nextcloud.models import CollectivePage, PageSubtype
from lib.nextcloud.protocol import Group, Protocol

logger = logging.getLogger(__name__)


def parse_content(page: CollectivePage) -> None:
    """Parse metadata from the markdown content."""

    config = bot_config or BotConfig.load_config()

    if not page.content or not page.ocs or not config:
        return

    if page.ocs.fileName == "Readme.md" and Group.valid_name(page.title):
        page.subtype = PageSubtype.GROUP
        page.save()

        if page.id and page.ocs:
            group = Group(page_id=page.ocs.id)
            try:
                group = Group.get(group.build_id())
            except NotFound:
                pass
            group.update_from_page()
            group.save()

    elif any(
        kw in page.ocs.filePath.lower()
        for kw in config.organisation.protocol_subtype_keywords
    ):
        page.subtype = PageSubtype.PROTOCOL
        page.save()

        protocol = Protocol(page_id=page.ocs.id, date="")
        try:
            protocol.get(protocol.build_id())
        except NotFound:
            pass
        protocol.update_from_page()
        protocol.save()


def parse_pages() -> None:
    """Fetch all pages from CouchDB and parse their content."""

    for page in cast(List[CollectivePage], CollectivePage.get_all(limit=500)):
        parse_content(page)
        page.save()
        logger.info("Parsed content for page %s", page.id)
