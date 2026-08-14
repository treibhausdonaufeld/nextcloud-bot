"""Deliver bot notifications into the Matrix rooms of `matrix_rooms`.

`app.services.notify` addresses notifications by a logical channel name
("ag-struktur", "protokolle"). Since every group already owns a room named
after it, that channel name maps onto a room alias with the same slug rule
used when the rooms are created — so a notification can be delivered without
configuring anything per channel.

Only rooms that already exist are used: a notification never creates a
channel room (that is the group sync's job), so an unknown channel falls
through to the other notification backends.

Direct messages (`@user` channels, e.g. the protocol feedback the bot sends
to the person who wrote a protocol) are the exception: a one-to-one room
cannot pre-exist unless somebody opened it, so the bot reuses the DM
recorded in its own `m.direct` account data and creates one if there is
none.
"""

from __future__ import annotations

import logging
from typing import Optional

import markdown as markdown_lib
import nh3

from app.services.matrix import MatrixClient, MatrixError, matrix_enabled
from app.services.matrix_rooms import channel_slug
from app.settings import settings

logger = logging.getLogger(__name__)

_MD_EXTENSIONS = ["extra", "nl2br", "sane_lists"]


def render_markdown(text: str) -> str:
    """Render notification markdown to the HTML Matrix clients display.

    Notification bodies embed content from outside the bot (calendar event
    descriptions, wiki pages), so the result is sanitized.
    """
    return nh3.clean(markdown_lib.markdown(text, extensions=_MD_EXTENSIONS))


def channel_alias_localpart(channel: str) -> str:
    """Room alias localpart for a logical channel name ("" when unusable)."""
    slug = channel_slug(channel)
    return settings.matrix.room_prefix + slug if slug else ""


DIRECT_ACCOUNT_DATA = "m.direct"

# Memberships that mean the recipient is still reachable in a DM room.
_ACTIVE = ("join", "invite")


def direct_room(client: MatrixClient, user_id: str) -> Optional[str]:
    """The bot's DM room with `user_id`, created if there is none yet.

    Reuses what the bot's `m.direct` account data already records — so a DM
    the recipient started in their own client is used instead of opening a
    second one — and skips rooms the recipient has left.
    """
    direct = client.get_account_data(DIRECT_ACCOUNT_DATA)

    known = direct.get(user_id)
    known = [room for room in known if room] if isinstance(known, list) else []

    for room_id in known:
        try:
            if client.room_members(room_id).get(user_id) in _ACTIVE:
                return str(room_id)
        except MatrixError:
            # Room gone, or the bot is no longer in it — try the next one.
            logger.debug("Stale Matrix DM room %s for %s", room_id, user_id)

    room_id = client.create_dm_room(user_id)
    if not room_id:
        return None

    logger.info("Created Matrix DM room %s with %s", room_id, user_id)
    direct[user_id] = known + [room_id]
    try:
        client.set_account_data(DIRECT_ACCOUNT_DATA, direct)
    except MatrixError:
        # The message can still be delivered; only the bookkeeping failed,
        # which would make the next notification open another room.
        logger.warning("Could not record the Matrix DM room with %s", user_id)

    return room_id


def channel_room(client: MatrixClient, channel: str) -> Optional[str]:
    """Room id for a channel name: a DM for `@user`, else the channel room."""
    if channel.startswith("@"):
        return direct_room(client, client.user_id(channel))

    localpart = channel_alias_localpart(channel)
    if not localpart:
        return None

    alias = client.room_alias(localpart)
    room_id = client.resolve_alias(alias)
    if not room_id:
        logger.info("No Matrix room %s for channel %s, falling back", alias, channel)
    return room_id


def send_matrix_message(text: str, channel: str) -> bool:
    """Post a notification into the channel's Matrix room.

    `@user` channels are delivered as a direct message. Returns True when the
    message was delivered, False when Matrix is not configured or no room
    could be found — the caller then falls back to another backend.
    """
    if not matrix_enabled():
        return False

    try:
        client = MatrixClient.from_settings()

        room_id = channel_room(client, channel)
        if not room_id:
            return False

        client.send_message(room_id, body=text, formatted_body=render_markdown(text))
    except MatrixError:
        logger.exception("Failed to send Matrix notification to channel %s", channel)
        return False

    logger.info("Sent notification to Matrix room %s", channel)
    return True
