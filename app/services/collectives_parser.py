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

        # A page below an archive page describes a dissolved group; the sweep
        # in `remove_stale_groups()` retires it (and its subgroups).
        if Group.is_archived_path(page.full_path):
            return

        group = Group.fetch_one(page_id=page.page_id) or Group(page_id=page.page_id)
        group.update_from_page()

        # Record who gained or lost a role, dated by the page's own
        # modification time (idempotent, so re-parsing is safe).
        GroupRole.sync_group(group, timestamp=page.timestamp)


def remove_stale_groups() -> None:
    """Delete groups whose page is gone or has been archived.

    Two ways a group ceases to exist: its page is deleted in Nextcloud (the
    page row is already gone by the time this runs, see
    `delete_orphaned_pages`), or the page is moved below an archive page —
    which archives its subpages along with it, so subgroups retire too.

    `Group.before_remove()` ends the roles that were still open, dated by the
    time the bot noticed: unlike a content edit, moving or deleting a page in
    Nextcloud does not touch its modification time, so the page timestamp
    would put the end far too early. The role history itself is kept, so past
    (and now-ended) roles stay visible on the members page.
    """
    config = bot_config or BotConfig.load_config()
    if not config:
        return

    groups = Group.fetch(limit=1000)
    if not groups:
        return

    pages = {
        page.page_id: page
        for page in CollectivePage.fetch(
            limit=10000, page_id__in=[g.page_id for g in groups]
        )
    }

    for group in groups:
        page = pages.get(group.page_id)

        if page is None:
            logger.info(
                "Retiring group %s: its page is gone (page_id=%s)",
                group.name,
                group.page_id,
            )
            group.remove()
            continue

        if Group.is_archived_path(page.full_path):
            logger.info("Retiring archived group %s (%s)", group.name, page.full_path)
            group.remove()


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
