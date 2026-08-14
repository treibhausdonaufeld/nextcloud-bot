"""Deliver bot notifications into the Matrix rooms of `matrix_rooms`.

`app.services.notify` addresses notifications by a logical channel name
("ag-struktur", "protokolle"). Since every group already owns a room named
after it, that channel name maps onto a room alias with the same slug rule
used when the rooms are created — so a notification can be delivered without
configuring anything per channel.

Only rooms that already exist are used: a notification never creates a room
(that is the group sync's job), and direct messages (`@user` channels) are
not supported here, so both cases fall through to the other notification
backends.
"""

from __future__ import annotations

import logging

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


def send_matrix_message(text: str, channel: str) -> bool:
    """Post a notification into the channel's Matrix room.

    Returns True when the message was delivered, False when Matrix is not
    configured, the channel is a direct message, or no room exists for it —
    the caller then falls back to another backend.
    """
    if not matrix_enabled():
        return False

    if channel.startswith("@"):
        # Direct messages need a per-user room; not supported here.
        logger.debug("Matrix: skipping direct message to %s", channel)
        return False

    localpart = channel_alias_localpart(channel)
    if not localpart:
        return False

    try:
        client = MatrixClient.from_settings()
        alias = client.room_alias(localpart)

        room_id = client.resolve_alias(alias)
        if not room_id:
            logger.info(
                "No Matrix room %s for channel %s, falling back", alias, channel
            )
            return False

        client.send_message(room_id, body=text, formatted_body=render_markdown(text))
    except MatrixError:
        logger.exception("Failed to send Matrix notification to channel %s", channel)
        return False

    logger.info("Sent notification to Matrix room %s", channel)
    return True
