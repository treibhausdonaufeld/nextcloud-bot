"""Keep a public Matrix room per active group (and its extra channels).

For every group page the bot parses, a public room is created from the group
name — "AG Struktur" becomes `#ag-struktur:example.com` — and everybody the
page names (coordination, delegates, members) is invited to it. Naming extra
channels on the page ("Chat-Kanäle: Fragen an AG Struktur") creates
`#fragen-an-ag-struktur:example.com` alongside it, with the same members.

Two rules shape the sync:

* it only ever **adds**. Anyone who already has a membership event in the
  room — joined, invited, or having left again — is left alone, so leaving a
  room is not undone by the next edit of the wiki page.
* it is **idempotent** and runs on every parsed group page (see
  `app.services.collectives_parser.parse_groups`), so an edit that adds a
  member or a channel is picked up on the next sync.

Everything here is a no-op unless a Matrix homeserver URL and an admin token
are configured (`settings.matrix`).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional
from urllib.parse import quote

from app.services.matrix import (
    KNOWN_MEMBERSHIPS,
    MatrixClient,
    MatrixError,
    matrix_enabled,
)
from app.settings import settings

if TYPE_CHECKING:
    from app.models.group import Group
    from app.models.user import NCUserList

logger = logging.getLogger(__name__)

# Transliterations that plain unicode decomposition gets wrong for German
# (and the Nordic letters that occasionally show up in names).
TRANSLITERATIONS = {
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "ß": "ss",
    "æ": "ae",
    "ø": "oe",
    "å": "aa",
}

# Characters allowed in a Matrix user id localpart.
USER_LOCALPART_RE = re.compile(r"[^a-z0-9._=\-+/]")


@dataclass(frozen=True)
class Channel:
    """A room to keep in sync: its alias localpart and its display name."""

    slug: str
    name: str


def channel_slug(name: str) -> str:
    """Turn a group or channel name into a room alias localpart.

    "AG Struktur" -> "ag-struktur", "Fragen an AG Struktur" ->
    "fragen-an-ag-struktur".
    """
    text = name.strip().lower()
    for source, target in TRANSLITERATIONS.items():
        text = text.replace(source, target)
    # Strip the remaining accents (é -> e) before dropping everything that is
    # not a plain ascii letter or digit.
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def group_channels(group: "Group") -> List[Channel]:
    """All channels of a group: its own room plus the extra ones from the page."""
    channels: List[Channel] = []
    seen: set[str] = set()

    for name in [group.name, *(group.chat_channels or [])]:
        slug = channel_slug(name or "")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        channels.append(Channel(slug=settings.matrix.room_prefix + slug, name=name))

    return channels


def group_channel_links(group: "Group") -> List[Dict[str, str]]:
    """Alias and matrix.to link of every chat channel of the group.

    Empty while Matrix is not configured, so the web UI simply hides the
    section instead of linking to rooms that do not exist.
    """
    if not matrix_enabled():
        return []

    server = settings.matrix.server_name
    return [
        {
            "alias": f"#{channel.slug}:{server}",
            "url": f"https://matrix.to/#/{quote(f'#{channel.slug}:{server}', safe='')}",
        }
        for channel in group_channels(group)
    ]


class MatrixRoomSync:
    """Creates the rooms of a group and invites its members."""

    def __init__(
        self,
        client: Optional[MatrixClient] = None,
        userlist: Optional["NCUserList"] = None,
    ):
        self.client = client or MatrixClient.from_settings()
        self._userlist = userlist

    @property
    def userlist(self) -> "NCUserList":
        if self._userlist is None:
            from app.models.user import NCUserList

            self._userlist = NCUserList()
        return self._userlist

    # --- users --------------------------------------------------------------

    def matrix_id(self, username: str) -> str:
        """Matrix id of a Nextcloud user (`@fabian.helm:example.com`).

        Accounts are provisioned from the same identity provider as
        Rocket.Chat, so the authentik username is the localpart.
        """
        localpart = USER_LOCALPART_RE.sub(
            "", self.userlist.chat_username(username).lower()
        )
        return self.client.user_id(localpart) if localpart else ""

    def matrix_ids(self, usernames: Iterable[str]) -> List[str]:
        ids = {self.matrix_id(name) for name in usernames}
        return sorted(user_id for user_id in ids if user_id)

    # --- rooms --------------------------------------------------------------

    def ensure_room(self, channel: Channel) -> Optional[str]:
        """Room id of the channel, creating the public room if needed."""
        alias = self.client.room_alias(channel.slug)

        room_id = self.client.resolve_alias(alias)
        if room_id:
            return room_id

        room_id = self.client.create_public_room(channel.slug, channel.name)
        if room_id:
            logger.info("Created Matrix room %s (%s)", alias, room_id)
        return room_id

    def room_members(self, room_id: str, alias: str) -> dict[str, str]:
        """Membership of the room, joining it first if that is what's missing."""
        try:
            return self.client.room_members(room_id)
        except MatrixError as exc:
            if exc.status not in (401, 403):
                raise
            # A room that exists but the bot is not part of (e.g. created by
            # hand before): join it, then the member list is readable.
            logger.info("Joining Matrix room %s to read its members", alias)
            self.client.join_room(alias)
            return self.client.room_members(room_id)

    def sync_channel(self, channel: Channel, member_ids: List[str]) -> int:
        """Create the room if needed and invite everyone who isn't in it yet.

        Returns the number of invitations sent.
        """
        alias = self.client.room_alias(channel.slug)

        room_id = self.ensure_room(channel)
        if not room_id:
            logger.warning("Could not create or resolve Matrix room %s", alias)
            return 0

        members = self.room_members(room_id, alias)

        invited = 0
        for user_id in member_ids:
            if members.get(user_id) in KNOWN_MEMBERSHIPS:
                continue
            try:
                self.client.invite(room_id, user_id)
            except MatrixError as exc:
                # A single unknown/deactivated account must not stop the rest.
                logger.warning("Could not invite %s to %s: %s", user_id, alias, exc)
                continue
            invited += 1
            logger.info("Invited %s to Matrix room %s", user_id, alias)

        return invited

    def sync_group(self, group: "Group") -> int:
        """Sync every channel of one group. Returns the invitations sent."""
        member_ids = self.matrix_ids(group.all_members)
        channels = group_channels(group)

        invited = 0
        for channel in channels:
            try:
                invited += self.sync_channel(channel, member_ids)
            except MatrixError:
                logger.exception(
                    "Matrix sync failed for channel %s of group %s",
                    channel.slug,
                    group.name,
                )
        return invited


def sync_group_rooms(group: "Group", sync: Optional[MatrixRoomSync] = None) -> None:
    """Entry point used by the parser: keep the group's chat rooms in sync.

    Does nothing unless Matrix is configured, and never lets a chat problem
    break the parsing of a page.
    """
    if sync is None:
        if not matrix_enabled():
            return
        try:
            sync = MatrixRoomSync()
        except MatrixError:
            logger.exception("Could not create the Matrix client")
            return

    try:
        sync.sync_group(group)
    except Exception:
        logger.exception("Matrix room sync failed for group %s", group.name)


def sync_all_groups() -> int:
    """Sync the rooms of every currently active group (manual/backfill run)."""
    from app.models.group import Group

    if not matrix_enabled():
        logger.info("Matrix is not configured, skipping chat room sync")
        return 0

    sync = MatrixRoomSync()
    groups = Group.fetch(limit=10000)
    for group in groups:
        sync_group_rooms(group, sync=sync)
    logger.info("Synced Matrix rooms for %d groups", len(groups))
    return len(groups)
