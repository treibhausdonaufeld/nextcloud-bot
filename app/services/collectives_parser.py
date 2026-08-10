"""Classify Collectives pages into Groups and Protocols and parse them."""

from __future__ import annotations

import logging

from app.models.collective_page import CollectivePage, PageSubtype
from app.models.group import Group
from app.models.group_role import GroupRole
from app.models.kv import get_state, set_state
from app.models.protocol import Protocol
from app.services.config import BotConfig, bot_config

logger = logging.getLogger(__name__)

# Marks the one-off seeding of `group_roles` from the already parsed groups.
ROLE_BACKFILL_STATE_KEY = "group_roles_backfilled"


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

        # Record who gained or lost a role, dated by the page's own
        # modification time (idempotent, so re-parsing is safe).
        GroupRole.sync_group(group, timestamp=page.timestamp)


def backfill_role_history() -> None:
    """Seed the role history for groups parsed before it was recorded.

    Group pages are only re-parsed when they change, so without this an
    existing installation would show no roles at all until every group page
    happens to be edited. Once every stored group has been walked the fact is
    persisted in `KVState`, so later iterations cost a single lookup; groups
    that appear afterwards go through `parse_groups` anyway.
    """
    if get_state(ROLE_BACKFILL_STATE_KEY):
        return

    known_pages = {row.page_id for row in GroupRole.all_rows()}

    for group in Group.fetch(limit=1000):
        if group.page_id in known_pages:
            continue
        page = CollectivePage.get_from_page_id_or_none(group.page_id)
        GroupRole.sync_group(group, timestamp=page.timestamp if page else None)

    set_state(ROLE_BACKFILL_STATE_KEY, {"done": True})
    logger.info("Role history backfill complete")


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
