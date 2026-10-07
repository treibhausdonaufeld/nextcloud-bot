"""Write side of the authentik integration: group membership.

``NCUserList.update_from_authentik`` reads users and their group memberships.
This module adds the mutation the bot needs: making a group's members match a
desired list of users. ``mailbox_sync`` uses it so everybody with a shared
mailbox is a member of the configured mail group (which is what grants access
to the mail app on the authentik side) — and nobody without one stays in it.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

import requests

from app.settings import settings

logger = logging.getLogger(__name__)

GROUPS_URL = "/api/v3/core/groups/"
USERS_URL = "/api/v3/core/users/"
TIMEOUT = 30


def authentik_configured() -> bool:
    """Whether a base URL and a token are configured for authentik."""
    return bool(settings.auth.authentik_base_url and settings.auth.authentik_token)


def _base_url() -> str:
    return str(settings.auth.authentik_base_url).rstrip("/")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.auth.authentik_token}",
        "Accept": "application/json",
    }


def find_group(name: str) -> Optional[dict]:
    """Return the authentik group whose name matches exactly, else None."""
    params: dict[str, str | int] = {"name": name, "page_size": 100}
    try:
        response = requests.get(
            _base_url() + GROUPS_URL,
            headers=_headers(),
            params=params,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Failed to look up authentik group %r: %s", name, e)
        return None

    for group in response.json().get("results", []):
        if group.get("name") == name:
            return group

    logger.warning("authentik group %r not found", name)
    return None


def _user_pk_by_uuid() -> dict[str, int] | None:
    """Map each authentik user's uuid to its numeric pk.

    The group membership endpoints address users by their numeric pk
    (``UserAccountSerializer.pk`` is an integer), while the bot knows users by
    their uuid (the Nextcloud uid). Fetch the user list once and build the
    lookup. Returns None when the fetch fails, so a network hiccup cannot be
    mistaken for "no users" and trigger mass removals.
    """
    mapping: dict[str, int] = {}
    page = 1
    while True:
        try:
            response = requests.get(
                _base_url() + USERS_URL,
                headers=_headers(),
                params={"page": page, "page_size": 500},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            logger.warning("Failed to fetch authentik users: %s", e)
            return None

        data = response.json()
        for result in data.get("results", []):
            user_uuid = result.get("uuid")
            pk = result.get("pk")
            if user_uuid and pk is not None:
                mapping[user_uuid] = pk

        next_page = (data.get("pagination") or {}).get("next") or 0
        if next_page <= page:
            break
        page = next_page
    return mapping


def _apply_membership(url: str, pks: Iterable[str | int], action: str) -> int:
    """Call authentik's add_user/remove_user endpoint once per user pk.

    Both endpoints accept a POST with ``{"pk": <numeric user pk>}``. Individual
    failures are logged and skipped, so one bad user does not abort the whole
    sync.
    """
    changed = 0
    for pk in sorted(pks):
        try:
            response = requests.post(
                url, headers=_headers(), json={"pk": pk}, timeout=TIMEOUT
            )
            response.raise_for_status()
        except requests.RequestException as e:
            logger.warning("Failed to %s user %s: %s", action, pk, e)
            continue
        changed += 1
    return changed


def sync_members(
    group_name: str,
    user_uuids: Iterable[str],
    remove_missing: bool = True,
) -> tuple[int, int]:
    """Make the named authentik group's members match ``user_uuids``.

    The Nextcloud username is the authentik uuid, so ``user_uuids`` are the
    ``NCUser.username`` values; they are resolved to the numeric user pks the
    membership endpoints expect. Missing users are added; when
    ``remove_missing`` is set, members that are not in the list are removed
    again. Returns ``(added, removed)``. Without a configured group name or
    authentik connection nothing happens, and an unknown group is a no-op.
    """
    if not group_name:
        return (0, 0)
    if not authentik_configured():
        logger.debug("authentik not configured, skipping group assignment")
        return (0, 0)

    group = find_group(group_name)
    if group is None:
        return (0, 0)

    group_uuid = group.get("pk")
    if not group_uuid:
        logger.warning("authentik group %r has no uuid", group_name)
        return (0, 0)

    desired_uuids = {uuid for uuid in user_uuids if uuid}
    members = set(group.get("users") or [])
    base = f"{_base_url()}{GROUPS_URL}{group_uuid}"

    if desired_uuids:
        pk_by_uuid = _user_pk_by_uuid()
        if pk_by_uuid is None:
            logger.warning(
                "Skipping authentik group %r: user lookup failed", group_name
            )
            return (0, 0)
    else:
        pk_by_uuid = {}

    desired: set[int] = set()
    unknown = []
    for user_uuid in sorted(desired_uuids):
        pk = pk_by_uuid.get(user_uuid)
        if pk is None:
            unknown.append(user_uuid)
        else:
            desired.add(pk)
    if unknown:
        logger.warning(
            "authentik group %r: unknown users %s", group_name, ", ".join(unknown)
        )

    added = _apply_membership(f"{base}/add_user/", desired - members, "add")
    removed = 0
    if remove_missing:
        removed = _apply_membership(f"{base}/remove_user/", members - desired, "remove")

    if added or removed:
        logger.info(
            "authentik group %r: %d added, %d removed", group_name, added, removed
        )
    return (added, removed)
