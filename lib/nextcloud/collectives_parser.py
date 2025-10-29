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
from chromadb.errors import NotFoundError
from typing import List, cast

from pycouchdb.exceptions import NotFound

from lib.nextcloud.config import BotConfig, bot_config
from lib.nextcloud.models.collective_page import CollectivePage, PageSubtype
from lib.nextcloud.models.group import Group
from lib.nextcloud.models.protocol import Protocol
from lib.chromadb import chroma_client

logger = logging.getLogger(__name__)


def parse_content(page: CollectivePage) -> None:
    """Parse metadata from the markdown content."""

    config = bot_config or BotConfig.load_config()

    try:
        protocol_collection = chroma_client.get_collection(name="protocols")
    except NotFoundError:
        protocol_collection = chroma_client.create_collection(name="protocols")

    if not page.content or not page.ocs or not config:
        return

    protocol_kws = set(config.organisation.protocol_subtype_keywords)

    if page.is_readme and Group.valid_name(page.title):
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
    elif (
        len(page.ocs.filePath.split("/")) > 1
        and (
            page.is_readme and page.ocs.filePath.split("/")[-2].lower() in protocol_kws
        )
        or (
            not page.is_readme
            and page.ocs.filePath.split("/")[-1].lower() in protocol_kws
        )
        # and Protocol.valid_title(page.title)
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

        protocol_collection.add(
            ids=[protocol.build_id()],
            documents=[page.content],
            metadatas=[
                {
                    "page_id": page.id,
                    "title": page.title,
                    "date": protocol.date,
                    "group_name": protocol.group_name,
                },
            ],
        )


def parse_pages() -> None:
    """Fetch all pages from CouchDB and parse their content."""

    for page in cast(List[CollectivePage], CollectivePage.get_all(limit=500)):
        parse_content(page)
        page.save()
        logger.info("Parsed content for page %s", page.id)
