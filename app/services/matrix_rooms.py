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
    MEMBER_POWER_LEVEL,
    OPEN_JOIN_RULE,
    PERMISSIVE_EVENT_POWER_LEVELS,
    MatrixClient,
    MatrixError,
    matrix_enabled,
)
from app.settings import settings
from app.textnorm import plain_name

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


def channels_for(names: Iterable[str]) -> List[Channel]:
    """Turn display names into channels, skipping empty and duplicate slugs.

    Names are reduced to plain text first, so markdown a page author wrote
    around a channel name never ends up as the room's title (this also fixes
    up groups parsed before that cleanup existed, without a re-parse).
    """
    channels: List[Channel] = []
    seen: set[str] = set()

    for raw in names:
        name = plain_name(raw or "")
        slug = channel_slug(name)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        channels.append(Channel(slug=settings.matrix.room_prefix + slug, name=name))

    return channels


def group_channels(group: "Group") -> List[Channel]:
    """All channels of a group: its own room plus the extra ones from the page."""
    return channels_for([group.name, *(group.chat_channels or [])])


def room_channel(name: str) -> Optional[Channel]:
    """One regular room from a config entry, or None when the name is empty."""
    channels = channels_for([name])
    return channels[0] if channels else None


def space_channel(name: str) -> Optional[Channel]:
    """A space from a display name, sharing the room prefix/slug rules.

    Spaces use the same slug as a room would, so `root_space: Alle` becomes
    `#alle:server` and `AG Haus-space` becomes `#ag-haus-space:server`.
    """
    channels = channels_for([name])
    return channels[0] if channels else None


# The organigram hides this special group and so does the automatic space
# creation; it is a visual grouping, not a top-level group.
EXCLUDED_TOPLEVEL_GROUPS = {"Großgruppe"}


def toplevel_groups(groups: Iterable["Group"]) -> List["Group"]:
    """The groups the organigram draws on the top level.

    That is the top group itself (`organisation.top_group_name`) plus every
    active group without a parent, excluding the visual "Großgruppe". These
    are the groups that get an automatic `-space` when configured.
    """
    from app.services.config import bot_config

    top_name = bot_config.organisation.top_group_name
    candidates = sorted(
        (group for group in groups if group.is_active),
        key=lambda group: (group.name.casefold(), group.name),
    )
    top = next((group for group in candidates if group.name == top_name), None)

    result: List["Group"] = [top] if top is not None else []
    result.extend(
        group
        for group in candidates
        if group is not top
        and not group.parent_group
        and group.name not in EXCLUDED_TOPLEVEL_GROUPS
    )
    return result


def parent_space_id(
    parent_name: str,
    auto_spaces: Dict[str, str],
    custom_spaces: Dict[str, str],
    groups_by_name: Dict[str, "Group"],
) -> str:
    """Space id of the nearest ancestor that has a space.

    `UG Mobilität` has no space of its own; its parent `AG Haus` has one, so
    the room belongs in `AG Haus-space`. Deeper levels walk up until they find
    an automatic top-level space or a custom `extra_spaces` entry.
    """
    seen: set[str] = set()
    name = (parent_name or "").strip()

    while name:
        key = plain_name(name).casefold()
        if not key or key in seen:
            break
        seen.add(key)

        if key in auto_spaces:
            return auto_spaces[key]
        if key in custom_spaces:
            return custom_spaces[key]

        parent = groups_by_name.get(key)
        name = parent.parent_group if parent and parent.parent_group else ""

    return ""


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
        Rocket.Chat, so the authentik username is the localpart. A chat
        username that already is a full matrix id (`@alice:other.example`,
        e.g. for someone on another homeserver) is used as it stands.
        """
        chat_name = self.userlist.chat_username(username).strip()
        if chat_name.startswith("@") and ":" in chat_name:
            return chat_name

        localpart = USER_LOCALPART_RE.sub("", chat_name.lstrip("@").lower())
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
            # Rooms created before this ran (or unlisted by hand) are put
            # back into the directory, so every channel stays findable.
            self.ensure_published(room_id, alias)
            return room_id

        room_id = self.client.create_public_room(channel.slug, channel.name)
        if room_id:
            logger.info("Created Matrix room %s (%s)", alias, room_id)
        return room_id

    def ensure_published(self, room_id: str, alias: str) -> None:
        """List the room in the server's public directory if it is not yet.

        Publishing can be refused by the homeserver's
        `room_list_publication_rules`; that must not stop the invitations, so
        the failure is only logged.
        """
        try:
            if self.client.directory_visibility(room_id) == "public":
                return
            self.client.publish_room(room_id)
            logger.info("Published Matrix room %s in the room directory", alias)
        except MatrixError as exc:
            logger.warning("Could not publish %s in the room directory: %s", alias, exc)

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

    def ensure_joined(self, room_id: str, alias: str) -> bool:
        """Make sure the bot is joined, so it may manage the room.

        The bot is the creator of everything it creates, but root spaces,
        channels and group spaces may already exist from an earlier setup or
        from manual work. Joining is idempotent and also accepts a pending
        invite. Returns False when joining was refused.
        """
        try:
            members = self.client.room_members(room_id)
            bot_id = self.client.whoami()
            if not bot_id or members.get(bot_id) == "join":
                return True
            logger.info("Joining Matrix room %s to manage it", alias)
            self.client.join_room(alias)
            return True
        except MatrixError as exc:
            if exc.status in (401, 403):
                try:
                    self.client.join_room(alias)
                    return True
                except MatrixError as join_exc:
                    logger.warning("Could not join Matrix room %s: %s", alias, join_exc)
                    return False
            logger.warning("Could not check membership of %s: %s", alias, exc)
            return False

    def ensure_permissions(self, room_id: str, alias: str) -> None:
        """Apply the permissive room policy to an existing room or space.

        Members may change name/topic/avatar, invite others and moderate
        content, but the address (`m.room.canonical_alias`), power levels,
        join rules, encryption and federation-related state stay with the bot.
        Existing explicit user levels are preserved; the bot is always made an
        admin. A bot that may not change the event (e.g. a room it did not
        create) only logs the failure — the invitation sync must not stop
        because of it.
        """
        try:
            content = self.client.get_state_event(room_id, "m.room.power_levels")
            desired = dict(content)

            # Do not add the bot to `users`: newer room versions reject the
            # creator appearing there ("Creator user ... must not appear in
            # content.users") and the creator already has ADMIN_POWER_LEVEL
            # implicitly. Explicit entries for other users are preserved.
            users = dict(desired.get("users") or {})
            bot_id = self.client.whoami()
            bot_level = users.get(bot_id, desired.get("users_default", 0))

            events = dict(desired.get("events") or {})
            for event_type, target in PERMISSIVE_EVENT_POWER_LEVELS.items():
                current = events.get(event_type)
                if current is not None and current > bot_level:
                    # Synapse refuses to change an event level above the
                    # sender's own level (e.g. a pre-existing tombstone at
                    # 150). Keeping the more restrictive value is safe.
                    continue
                events[event_type] = target

            desired.update(
                {
                    "users_default": MEMBER_POWER_LEVEL,
                    "invite": 0,
                    "kick": MEMBER_POWER_LEVEL,
                    "ban": MEMBER_POWER_LEVEL,
                    "redact": MEMBER_POWER_LEVEL,
                    "state_default": MEMBER_POWER_LEVEL,
                    "events": events,
                }
            )

            if desired == content:
                return
            self.client.put_state_event(room_id, "m.room.power_levels", desired)
            logger.info("Updated permissions in Matrix room %s", alias)
        except MatrixError as exc:
            logger.warning(
                "Could not update permissions in Matrix room %s: %s", alias, exc
            )

    def check_room_policy(self, room_id: str, alias: str) -> None:
        """Warn about existing rooms that violate the local/unencrypted policy.

        Federation and encryption are fixed when the room is created and
        cannot be changed afterwards, so the sync can only report these.
        """
        try:
            for problem in self.client.room_policy_violations(room_id):
                logger.warning("Matrix room %s: %s", alias, problem)
        except MatrixError as exc:
            logger.warning("Could not inspect Matrix room %s: %s", alias, exc)

    def ensure_joinable(self, room_id: str, alias: str) -> None:
        """Make sure members can join the room/space without an invite.

        Rooms the bot creates already start out public, but a room or space
        that existed before the sync (created by hand, imported, or left over
        from an older setup) may still be invite-only or restricted. Matrix
        lets us change `m.room.join_rules` after creation, so every sync
        reopens it. Whether a *local* account may use that public join rule is
        decided by the homeserver policy (the `group_gate` module), not here.
        Failures are only logged: the invitation sync must not stop because of
        a room the bot does not control.
        """
        try:
            content = self.client.get_state_event(room_id, "m.room.join_rules")
            if content.get("join_rule") == OPEN_JOIN_RULE:
                return
            self.client.put_state_event(
                room_id, "m.room.join_rules", {"join_rule": OPEN_JOIN_RULE}
            )
            logger.info(
                "Opened Matrix room %s to members (join_rule=%s)", alias, OPEN_JOIN_RULE
            )
        except MatrixError as exc:
            logger.warning("Could not open Matrix room %s to members: %s", alias, exc)

    def ensure_chat_room(self, channel: Channel) -> Optional[str]:
        """Create/resolve a room and apply the membership/permission policy.

        Used for rooms that are not tied to a member list (root channels and
        the children of custom spaces): no invitations are sent, but the room
        is public, listed in the directory and carries the common power
        levels.
        """
        alias = self.client.room_alias(channel.slug)
        room_id = self.ensure_room(channel)
        if not room_id:
            logger.warning("Could not create or resolve Matrix room %s", alias)
            return None

        if not self.ensure_joined(room_id, alias):
            return None
        self.ensure_permissions(room_id, alias)
        self.ensure_joinable(room_id, alias)
        self.check_room_policy(room_id, alias)
        return room_id

    def ensure_space(self, channel: Channel) -> Optional[str]:
        """Create/resolve a Matrix space and apply the shared room policy."""
        alias = self.client.room_alias(channel.slug)
        room_id = self.client.resolve_alias(alias)

        if room_id:
            if not self.ensure_joined(room_id, alias):
                return None
            try:
                if not self.client.room_is_space(room_id):
                    logger.warning(
                        "Matrix alias %s exists but is not a space; leaving it alone",
                        alias,
                    )
                    return None
            except MatrixError as exc:
                logger.warning("Could not inspect Matrix space %s: %s", alias, exc)
                return None
            self.ensure_published(room_id, alias)
        else:
            room_id = self.client.create_space(channel.slug, channel.name)
            if not room_id:
                logger.warning("Could not create Matrix space %s", alias)
                return None
            logger.info("Created Matrix space %s (%s)", alias, room_id)
            if not self.ensure_joined(room_id, alias):
                return None

        self.ensure_permissions(room_id, alias)
        self.ensure_joinable(room_id, alias)
        self.check_room_policy(room_id, alias)
        return room_id

    def link_space_child(self, parent_id: str, child_id: str, order: int = 0) -> None:
        """Put a room/space into a space and record the inverse parent link."""
        if not parent_id or not child_id or parent_id == child_id:
            return
        try:
            self.client.add_space_child(
                parent_id, child_id, order=f"{order:02d}" if order else ""
            )
            self.client.add_space_parent(child_id, parent_id)
        except MatrixError as exc:
            logger.warning(
                "Could not add %s to Matrix space %s: %s", child_id, parent_id, exc
            )

    def invite_members(self, room_id: str, alias: str, member_ids: List[str]) -> int:
        """Invite everyone who has no membership event in the room yet.

        Returns the number of invitations sent.
        """
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

    def sync_channel(self, channel: Channel, member_ids: List[str]) -> int:
        """Create the room if needed and invite everyone who isn't in it yet.

        Returns the number of invitations sent.
        """
        alias = self.client.room_alias(channel.slug)

        room_id = self.ensure_room(channel)
        if not room_id:
            logger.warning("Could not create or resolve Matrix room %s", alias)
            return 0

        if not self.ensure_joined(room_id, alias):
            return 0
        self.ensure_permissions(room_id, alias)
        self.ensure_joinable(room_id, alias)
        self.check_room_policy(room_id, alias)
        return self.invite_members(room_id, alias, member_ids)

    def sync_channels(
        self, channels: List[Channel], member_ids: List[str], context: str
    ) -> int:
        """Sync a list of channels; one failing channel does not stop the rest."""
        invited = 0
        for channel in channels:
            try:
                invited += self.sync_channel(channel, member_ids)
            except MatrixError:
                logger.exception(
                    "Matrix sync failed for channel %s (%s)", channel.slug, context
                )
        return invited

    def sync_group(self, group: "Group") -> int:
        """Sync every channel of one group. Returns the invitations sent.

        Only people who are members according to the authentik group filter
        (`organisation.authentik_group_membership_required`) are invited, even
        when the wiki page still names former members.
        """
        return self.sync_channels(
            group_channels(group),
            self.matrix_ids(self.userlist.filter_members(group.all_members)),
            context=f"group {group.name}",
        )

    def sync_chat_structure(self) -> None:
        """Create the configured space hierarchy (the `chat` config block).

        Layout:

        - `root_space` at the top,
        - `root_channels` directly inside it (and, with
          `root_channels_autojoin`, every member is invited to the space and
          the channels),
        - one automatic `<group>-space` per top-level organigram group (unless
          the group is listed in an `extra_spaces` entry), containing the
          group's rooms,
        - every `extra_spaces` key as a space, its children as regular rooms,
          attached to the root space,
        - with `room_per_group`, a room for every group, with subgroup rooms
          nested in the nearest ancestor space ("UG Mobilität" under
          "AG Haus-space").
        """
        from app.models.group import Group
        from app.services.config import bot_config

        try:
            chat = bot_config.chat
        except Exception:
            logger.exception("Could not load the Matrix chat configuration")
            return

        root = space_channel(chat.root_space)
        if root is None:
            logger.debug("No Matrix root_space configured, skipping space sync")
            return

        root_id = self.ensure_space(root)
        if not root_id:
            logger.warning("Could not create/resolve root Matrix space %s", root.name)
            return

        # With `root_channels_autojoin`, everyone in the configured authentik
        # member groups is invited to the root space and to each root channel.
        # The bot cannot make another user join, so this sends invites.
        autojoin_ids: List[str] = []
        if chat.root_channels_autojoin:
            autojoin_ids = self.matrix_ids(
                user.username for user in self.userlist.get_member_users()
            )
            if not autojoin_ids:
                logger.warning(
                    "No members to invite to the Matrix root space and channels"
                )

        def autojoin(room_id: str, channel: Channel) -> None:
            if not autojoin_ids:
                return
            alias = self.client.room_alias(channel.slug)
            try:
                self.invite_members(room_id, alias, autojoin_ids)
            except MatrixError:
                logger.exception("Could not invite members to Matrix room %s", alias)

        autojoin(root_id, root)

        order = 0

        # General rooms directly below the root space.
        for channel in channels_for(chat.root_channels):
            room_id = self.ensure_chat_room(channel)
            if room_id:
                order += 1
                self.link_space_child(root_id, room_id, order=order)
                autojoin(room_id, channel)

        extra_children = {
            plain_name(name).casefold()
            for names in chat.extra_spaces.values()
            for name in names
            if plain_name(name)
        }

        active_groups = (
            Group.fetch_active(limit=10000)
            if chat.space_per_toplevel_group or chat.room_per_group
            else []
        )
        groups_by_name = {group.name.casefold(): group for group in active_groups}
        auto_spaces: Dict[str, str] = {}
        custom_spaces: Dict[str, str] = {}
        group_room_ids: Dict[str, str] = {}

        # One space per top-level group, containing that group's rooms.
        if chat.space_per_toplevel_group:
            for group in toplevel_groups(active_groups):
                if group.name.casefold() in extra_children:
                    logger.info(
                        "Skipping automatic space for %s: listed in extra_spaces",
                        group.name,
                    )
                    continue

                space = space_channel(f"{group.name}-space")
                if space is None:
                    continue
                space_id = self.ensure_space(space)
                if not space_id:
                    continue
                auto_spaces[group.name.casefold()] = space_id

                child_order = 0
                group_channels_list = group_channels(group)
                for index, channel in enumerate(group_channels_list):
                    room_id = self.ensure_chat_room(channel)
                    if room_id:
                        child_order += 1
                        self.link_space_child(space_id, room_id, order=child_order)
                        if index == 0:
                            group_room_ids[group.name.casefold()] = room_id

                order += 1
                self.link_space_child(root_id, space_id, order=order)

        # Manually curated spaces and their regular rooms.
        for space_name, child_names in chat.extra_spaces.items():
            extra_space = space_channel(space_name)
            if extra_space is None:
                continue
            extra_space_id = self.ensure_space(extra_space)
            if not extra_space_id:
                continue

            child_order = 0
            for child_name in child_names:
                child_channel = room_channel(child_name)
                if child_channel is None:
                    continue
                room_id = self.ensure_chat_room(child_channel)
                if room_id:
                    child_order += 1
                    self.link_space_child(extra_space_id, room_id, order=child_order)
                    custom_spaces[plain_name(child_name).casefold()] = extra_space_id

            order += 1
            self.link_space_child(root_id, extra_space_id, order=order)

        # Optionally create a room for every group and nest subgroup rooms
        # under the nearest ancestor space ("UG Mobilität" -> AG Haus-space).
        if chat.room_per_group:
            for group in sorted(
                active_groups, key=lambda item: (item.name.casefold(), item.name)
            ):
                group_channels_list = group_channels(group)
                if not group_channels_list:
                    continue

                key = group.name.casefold()
                room_id = group_room_ids.get(key)
                if not room_id:
                    room_id = self.ensure_chat_room(group_channels_list[0])
                    if room_id:
                        group_room_ids[key] = room_id
                if not room_id or not group.parent_group:
                    continue

                target_space = parent_space_id(
                    group.parent_group, auto_spaces, custom_spaces, groups_by_name
                )
                if target_space:
                    self.link_space_child(target_space, room_id)
                else:
                    logger.debug(
                        "No parent space for group %s, leaving its room unlinked",
                        group.name,
                    )


def _room_sync(userlist: Optional["NCUserList"] = None) -> Optional[MatrixRoomSync]:
    """Build a sync, or None when Matrix is not configured/reachable."""
    if not matrix_enabled():
        return None
    try:
        return MatrixRoomSync(userlist=userlist)
    except MatrixError:
        logger.exception("Could not create the Matrix client")
        return None


def sync_group_rooms(group: "Group", sync: Optional[MatrixRoomSync] = None) -> None:
    """Entry point used by the parser: keep the group's chat rooms in sync.

    Does nothing unless Matrix is configured, and never lets a chat problem
    break the parsing of a page.
    """
    sync = sync or _room_sync()
    if sync is None:
        return

    try:
        sync.sync_group(group)
    except Exception:
        logger.exception("Matrix room sync failed for group %s", group.name)


def sync_chat_structure(sync: Optional[MatrixRoomSync] = None) -> None:
    """Entry point used by the worker: reconcile the configured space tree.

    The structure lives in the bot-config `chat` block and is independent of
    a single group page, so it is reconciled once per worker iteration. Never
    raises: a chat outage must not break the rest of the sync.
    """
    if sync is None and not matrix_enabled():
        return

    sync = sync or _room_sync()
    if sync is None:
        return

    try:
        sync.sync_chat_structure()
    except Exception:
        logger.exception("Matrix chat structure sync failed")


def sync_all_groups() -> int:
    """Sync all group rooms and the configured space tree."""
    from app.models.group import Group

    sync = _room_sync()
    if sync is None:
        logger.info("Matrix is not configured, skipping chat room sync")
        return 0

    sync_chat_structure(sync=sync)

    # Retired groups are skipped: their room stays where it is, but nobody is
    # invited into a group that no longer exists.
    groups = Group.fetch_active(limit=10000)
    for group in groups:
        sync_group_rooms(group, sync=sync)
    logger.info("Synced Matrix rooms for %d groups", len(groups))
    return len(groups)
