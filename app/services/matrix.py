"""Thin wrapper around the Matrix Client-Server API.

Only the handful of endpoints the group chat room sync needs is covered:
resolving a room alias, creating a public room, reading the membership of a
room and inviting users. Every call goes out with the admin access token from
`settings.matrix`; the feature is inert unless a homeserver URL and a token
are configured (`settings.matrix.enabled`).

Rate limits (`M_LIMIT_EXCEEDED`) are retried a few times honouring the
server's `retry_after_ms`, everything else surfaces as `MatrixError`.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from uuid import uuid4

import requests

from app.settings import settings

logger = logging.getLogger(__name__)

API = "/_matrix/client/v3"

TIMEOUT = 30
MAX_RATE_LIMIT_RETRIES = 3

# Membership states that mean "this user has already been dealt with". The
# bot only ever adds people, so anybody who left (or was kicked/banned) is
# left alone instead of being invited again on the next page edit.
KNOWN_MEMBERSHIPS = ("join", "invite", "knock", "leave", "ban")

# Power levels used in bot-managed rooms and spaces. Members are deliberately
# trusted with day-to-day room management (title, topic, invites, kicking and
# redacting) but not with changing the room address or the power levels
# themselves — otherwise they could escalate or make the room unfindable.
ADMIN_POWER_LEVEL = 100
MEMBER_POWER_LEVEL = 50

# Join rule of every bot-managed room/space. `public` means anybody on this
# homeserver may join without an invite; who is allowed to do so is a Synapse
# policy question (see the `group_gate` module), not a per-room one.
OPEN_JOIN_RULE = "public"

# State events a normal member may change. `m.room.canonical_alias` is pinned
# to the admin level so the address stays stable, and `m.room.join_rules` /
# `m.room.guest_access` so a member cannot accidentally close the room to
# everybody else.
PERMISSIVE_EVENT_POWER_LEVELS: Dict[str, int] = {
    "m.room.name": 0,
    "m.room.topic": 0,
    "m.room.avatar": 0,
    "m.room.canonical_alias": ADMIN_POWER_LEVEL,
    "m.room.power_levels": ADMIN_POWER_LEVEL,
    "m.room.encryption": ADMIN_POWER_LEVEL,
    "m.room.tombstone": ADMIN_POWER_LEVEL,
    "m.room.server_acl": ADMIN_POWER_LEVEL,
    "m.room.join_rules": ADMIN_POWER_LEVEL,
    "m.room.guest_access": ADMIN_POWER_LEVEL,
}


def permissive_power_levels() -> Dict[str, Any]:
    """The `m.room.power_levels` content shared by every managed room/space.

    Members may rename the room, change its topic/avatar and invite others
    (`invite: 0`); they can also kick/ban/redact. Only the bot (admin level
    100) may change the address, power levels, encryption or federation
    related state. Existing per-user entries are preserved by the caller when
    this is merged into an existing event.
    """
    return {
        "users_default": MEMBER_POWER_LEVEL,
        "invite": 0,
        "kick": MEMBER_POWER_LEVEL,
        "ban": MEMBER_POWER_LEVEL,
        "redact": MEMBER_POWER_LEVEL,
        "state_default": MEMBER_POWER_LEVEL,
        "events": dict(PERMISSIVE_EVENT_POWER_LEVELS),
    }


class MatrixError(RuntimeError):
    """A Matrix API call failed."""

    def __init__(self, message: str, status: int = 0, errcode: str = ""):
        super().__init__(message)
        self.status = status
        self.errcode = errcode


def matrix_enabled() -> bool:
    """Whether a homeserver and an admin token are configured."""
    return settings.matrix.enabled


class MatrixClient:
    """Minimal Matrix client speaking the Client-Server API v3."""

    def __init__(
        self,
        homeserver_url: str,
        access_token: str,
        server_name: str,
        user_domain: str = "",
    ):
        self.base_url = homeserver_url.rstrip("/")
        self.access_token = access_token
        self.server_name = server_name
        self.user_domain = user_domain or server_name
        self._user_id = ""
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }
        )

    @classmethod
    def from_settings(cls) -> "MatrixClient":
        if not settings.matrix.enabled:
            raise MatrixError("Matrix is not configured")
        return cls(
            homeserver_url=str(settings.matrix.homeserver_url),
            access_token=settings.matrix.admin_token,
            server_name=settings.matrix.server_name,
            user_domain=settings.matrix.user_domain,
        )

    # --- plumbing -----------------------------------------------------------

    def _request(
        self, method: str, path: str, payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"

        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                response = self.session.request(
                    method, url, json=payload, timeout=TIMEOUT
                )
            except requests.RequestException as exc:
                raise MatrixError(f"{method} {path} failed: {exc}") from exc

            try:
                body = response.json() if response.content else {}
            except ValueError:
                body = {}

            # The spec answers these endpoints with 200, but homeservers and
            # reverse proxies do return other 2xx codes (201/204) — treat the
            # whole range as success.
            if 200 <= response.status_code < 300:
                return body if isinstance(body, dict) else {}

            errcode = str(body.get("errcode", "")) if isinstance(body, dict) else ""

            if errcode == "M_LIMIT_EXCEEDED" and attempt < MAX_RATE_LIMIT_RETRIES:
                delay = float(body.get("retry_after_ms", 1000)) / 1000
                logger.debug("Matrix rate limited, retrying in %.1fs", delay)
                time.sleep(min(delay, 10))
                continue

            raise MatrixError(
                f"{method} {path} failed ({response.status_code} {errcode}): "
                f"{body.get('error', response.text[:200]) if isinstance(body, dict) else ''}",
                status=response.status_code,
                errcode=errcode,
            )

        raise MatrixError(f"{method} {path} failed: rate limited")

    # --- ids ----------------------------------------------------------------

    def room_alias(self, localpart: str) -> str:
        """Full room alias for a channel name, e.g. `#ag-struktur:example.com`."""
        return f"#{localpart}:{self.server_name}"

    def user_id(self, localpart: str) -> str:
        """Full user id for a chat username, e.g. `@alice:example.com`."""
        if localpart.startswith("@"):
            return localpart if ":" in localpart else f"{localpart}:{self.user_domain}"
        return f"@{localpart}:{self.user_domain}"

    def whoami(self) -> str:
        """User id this token belongs to (cached for the client's lifetime)."""
        if not self._user_id:
            body = self._request("GET", f"{API}/account/whoami")
            self._user_id = str(body.get("user_id", ""))
        return self._user_id

    # --- account data -------------------------------------------------------

    def get_account_data(self, event_type: str) -> Dict[str, Any]:
        """Account data of the bot user; `{}` when it was never set."""
        user_id = self.whoami()
        try:
            return self._request(
                "GET",
                f"{API}/user/{quote(user_id, safe='')}/account_data/{event_type}",
            )
        except MatrixError as exc:
            if exc.status == 404 or exc.errcode == "M_NOT_FOUND":
                return {}
            raise

    def set_account_data(self, event_type: str, content: Dict[str, Any]) -> None:
        user_id = self.whoami()
        self._request(
            "PUT",
            f"{API}/user/{quote(user_id, safe='')}/account_data/{event_type}",
            content,
        )

    # --- rooms --------------------------------------------------------------

    def resolve_alias(self, alias: str) -> Optional[str]:
        """Room id behind a room alias, or None when the alias is unused."""
        try:
            body = self._request("GET", f"{API}/directory/room/{quote(alias, safe='')}")
        except MatrixError as exc:
            if exc.status == 404 or exc.errcode == "M_NOT_FOUND":
                return None
            raise
        room_id = body.get("room_id")
        return str(room_id) if room_id else None

    def create_dm_room(self, user_id: str) -> Optional[str]:
        """Create a private one-to-one room and invite `user_id` into it."""
        body = self._request(
            "POST",
            f"{API}/createRoom",
            {
                "is_direct": True,
                "preset": "trusted_private_chat",
                "invite": [user_id],
            },
        )
        room_id = body.get("room_id")
        return str(room_id) if room_id else None

    def create_public_room(
        self, localpart: str, name: str, topic: str = ""
    ) -> Optional[str]:
        """Create a public, server-local room and list it in the directory.

        `public_chat` makes the room joinable by anyone on the homeserver
        without an invite, `visibility: public` lists it in the room
        directory so every local user can find it by searching. History stays
        `shared` (readable by members from the room's start, not by anonymous
        outsiders), and `m.federate: false` keeps the room on this homeserver
        — note that this can only be decided at creation time.

        `power_level_content_override` follows `permissive_power_levels()`:
        every member may change name/topic/avatar and invite others, while
        only the bot may change the address or the power levels. No
        `m.room.encryption` initial state is sent, so the room stays
        unencrypted.

        Returns the new room id, or the existing one if another process won
        the race for the alias.
        """
        return self._create_room(localpart, name, topic=topic)

    def create_space(self, localpart: str, name: str, topic: str = "") -> Optional[str]:
        """Create a public, server-local Matrix space (type `m.space`).

        Spaces use the same permissive permissions and local-only/federated
        policy as rooms; the only difference is the `type: m.space` creation
        content. Children are attached afterwards with `add_space_child()`.
        """
        return self._create_room(localpart, name, topic=topic, room_type="m.space")

    def _create_room(
        self,
        localpart: str,
        name: str,
        topic: str = "",
        room_type: str = "",
    ) -> Optional[str]:
        creation_content: Dict[str, Any] = {"m.federate": False}
        if room_type:
            creation_content["type"] = room_type

        payload: Dict[str, Any] = {
            "room_alias_name": localpart,
            "name": name,
            "visibility": "public",
            "preset": "public_chat",
            "creation_content": creation_content,
            "power_level_content_override": permissive_power_levels(),
            "initial_state": [
                {
                    "type": "m.room.join_rules",
                    "state_key": "",
                    "content": {"join_rule": OPEN_JOIN_RULE},
                },
                {
                    "type": "m.room.history_visibility",
                    "state_key": "",
                    "content": {"history_visibility": "shared"},
                },
                {
                    "type": "m.room.guest_access",
                    "state_key": "",
                    "content": {"guest_access": "forbidden"},
                },
            ],
        }
        if topic:
            payload["topic"] = topic

        try:
            body = self._request("POST", f"{API}/createRoom", payload)
        except MatrixError as exc:
            if exc.errcode == "M_ROOM_IN_USE":
                # Alias taken between the lookup and the create call.
                return self.resolve_alias(self.room_alias(localpart))
            raise

        room_id = body.get("room_id")
        return str(room_id) if room_id else None

    def join_room(self, room_id_or_alias: str) -> Optional[str]:
        body = self._request("POST", f"{API}/join/{quote(room_id_or_alias, safe='')}")
        room_id = body.get("room_id")
        return str(room_id) if room_id else None

    def directory_visibility(self, room_id: str) -> str:
        """Whether the room is listed in this server's public room directory."""
        body = self._request(
            "GET", f"{API}/directory/list/room/{quote(room_id, safe='')}"
        )
        return str(body.get("visibility", ""))

    def publish_room(self, room_id: str) -> None:
        """List the room in this server's public room directory."""
        self._request(
            "PUT",
            f"{API}/directory/list/room/{quote(room_id, safe='')}",
            {"visibility": "public"},
        )

    # --- spaces -------------------------------------------------------------

    def add_space_child(self, space_id: str, child_id: str, order: str = "") -> None:
        """Attach a room or space to a space (`m.space.child`).

        The current event is read first so an already-correct link is not
        rewritten on every sync (each write would add another state event to
        the room).
        """
        content: Dict[str, Any] = {"via": [self.server_name]}
        if order:
            content["order"] = order
        existing = self.get_state_event(space_id, "m.space.child", child_id)
        if existing.get("via") == content["via"] and existing.get(
            "order"
        ) == content.get("order"):
            return
        self._request(
            "PUT",
            f"{API}/rooms/{quote(space_id, safe='')}"
            f"/state/m.space.child/{quote(child_id, safe='')}",
            content,
        )

    def add_space_parent(self, child_id: str, parent_id: str) -> None:
        """Record the inverse hierarchy link in the child (`m.space.parent`)."""
        content: Dict[str, Any] = {"via": [self.server_name]}
        existing = self.get_state_event(child_id, "m.space.parent", parent_id)
        if existing.get("via") == content["via"]:
            return
        self._request(
            "PUT",
            f"{API}/rooms/{quote(child_id, safe='')}"
            f"/state/m.space.parent/{quote(parent_id, safe='')}",
            content,
        )

    def room_is_space(self, room_id: str) -> bool:
        """Whether the room's creation event declares it as a Matrix space."""
        create = self.get_state_event(room_id, "m.room.create")
        return create.get("type") == "m.space"

    def room_policy_violations(self, room_id: str) -> List[str]:
        """Describe immutable room properties that do not match our policy.

        Federation and encryption can only be *created* correctly: Matrix has
        no supported way to un-federate or un-encrypt an existing room. This
        helper lets the sync log violations instead of silently believing it
        fixed them.
        """
        problems: List[str] = []
        create = self.get_state_event(room_id, "m.room.create")
        if create.get("m.federate", True) is not False:
            problems.append(
                "room is federated (m.federate can only be set at creation)"
            )
        if self.get_state_event(room_id, "m.room.encryption"):
            problems.append("room is encrypted (encryption cannot be disabled)")
        return problems

    def room_members(self, room_id: str) -> Dict[str, str]:
        """Map user id -> membership for every member event of the room."""
        body = self._request("GET", f"{API}/rooms/{quote(room_id, safe='')}/members")
        members: Dict[str, str] = {}
        for event in body.get("chunk", []):
            user_id = event.get("state_key")
            membership = (event.get("content") or {}).get("membership")
            if user_id and membership:
                members[str(user_id)] = str(membership)
        return members

    def get_state_event(
        self, room_id: str, event_type: str, state_key: str = ""
    ) -> Dict[str, Any]:
        """Content of a room state event; `{}` if unset.

        With an empty `state_key` the endpoint for the empty-key event is
        used; pass a state key for events such as `m.space.child` /
        `m.space.parent`.
        """
        path = (
            f"{API}/rooms/{quote(room_id, safe='')}/state/{quote(event_type, safe='')}"
        )
        if state_key:
            path += f"/{quote(state_key, safe='')}"
        try:
            return self._request("GET", path)
        except MatrixError as exc:
            if exc.status == 404 or exc.errcode == "M_NOT_FOUND":
                return {}
            raise

    def put_state_event(
        self, room_id: str, event_type: str, content: Dict[str, Any]
    ) -> None:
        """Replace a room state event with an empty state key."""
        self._request(
            "PUT",
            f"{API}/rooms/{quote(room_id, safe='')}/state/{quote(event_type, safe='')}",
            content,
        )

    def send_message(self, room_id: str, body: str, formatted_body: str = "") -> None:
        """Send an `m.text` message into the room.

        `formatted_body` (HTML) is attached when given; clients that cannot
        render it fall back to the plain `body`.
        """
        content: Dict[str, Any] = {"msgtype": "m.text", "body": body}
        if formatted_body:
            content["format"] = "org.matrix.custom.html"
            content["formatted_body"] = formatted_body

        # The transaction id makes the send idempotent if a retry ever races.
        txn_id = uuid4().hex
        self._request(
            "PUT",
            f"{API}/rooms/{quote(room_id, safe='')}/send/m.room.message/{txn_id}",
            content,
        )

    def invite(self, room_id: str, user_id: str) -> None:
        self._request(
            "POST",
            f"{API}/rooms/{quote(room_id, safe='')}/invite",
            {"user_id": user_id},
        )
