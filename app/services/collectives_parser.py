"""Classify Collectives pages into Groups and Protocols and parse them."""

from __future__ import annotations

import logging

from app.models.collective_page import CollectivePage, PageSubtype
from app.models.group import Group
from app.models.protocol import Protocol
from app.services.config import BotConfig, bot_config

logger = logging.getLogger(__name__)


def parse_groups(page: CollectivePage) -> None:
    """Parse metadata from the markdown content."""

    config = bot_config or BotConfig.load_config()

    if not page.content or not config:
        return

    if Group.valid_name(page.title):
        if page.subtype != PageSubtype.GROUP:
            page.subtype = PageSubtype.GROUP
            page.store()

        group = Group.fetch_one(page_id=page.page_id) or Group(page_id=page.page_id)
        group.update_from_page()


def parse_protocols(page: CollectivePage) -> None:
    config = bot_config or BotConfig.load_config()

    if not page.content or not config:
        return

    if Protocol.is_protocol_page(page):
        if page.subtype != PageSubtype.PROTOCOL:
            page.subtype = PageSubtype.PROTOCOL
            page.store()

        # Backfill version history and media for pages stored before the
        # versioning feature existed (both calls are idempotent).
        from app.services.collectives_loader import snapshot_protocol_page

        snapshot_protocol_page(page)

        protocol = Protocol.fetch_one(page_id=page.page_id) or Protocol(
            page_id=page.page_id, date=""
        )
        protocol.update_from_page()
