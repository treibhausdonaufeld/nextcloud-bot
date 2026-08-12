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
from typing import Any, Dict, Optional
from urllib.parse import quote

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

            if response.status_code == 200:
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

    def create_public_room(
        self, localpart: str, name: str, topic: str = ""
    ) -> Optional[str]:
        """Create a public room and publish it in the room directory.

        `public_chat` makes the room joinable by anyone on the homeserver
        without an invite, `visibility: public` lists it in the directory.
        History stays `shared` (readable by members from the room's start,
        not by anonymous outsiders).

        Returns the new room id, or the existing one if another process won
        the race for the alias.
        """
        payload: Dict[str, Any] = {
            "room_alias_name": localpart,
            "name": name,
            "visibility": "public",
            "preset": "public_chat",
            "initial_state": [
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

    def invite(self, room_id: str, user_id: str) -> None:
        self._request(
            "POST",
            f"{API}/rooms/{quote(room_id, safe='')}/invite",
            {"user_id": user_id},
        )
