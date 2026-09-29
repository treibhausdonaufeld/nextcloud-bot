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


def _apply_membership(url: str, uuids: Iterable[str], action: str) -> int:
    """Call authentik's add_user/remove_user endpoint once per uuid.

    Both endpoints accept a POST with ``{"pk": <uuid>}``. Individual failures
    are logged and skipped, so one bad user does not abort the whole sync.
    """
    changed = 0
    for uuid in sorted(uuids):
        try:
            response = requests.post(
                url, headers=_headers(), json={"pk": uuid}, timeout=TIMEOUT
            )
            response.raise_for_status()
        except requests.RequestException as e:
            logger.warning("Failed to %s user %s: %s", action, uuid, e)
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
    ``NCUser.username`` values. Missing users are added; when
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

    desired = {uuid for uuid in user_uuids if uuid}
    members = set(group.get("users") or [])
    base = f"{_base_url()}{GROUPS_URL}{group_uuid}"

    added = _apply_membership(f"{base}/add_user/", desired - members, "add")
    removed = 0
    if remove_missing:
        removed = _apply_membership(f"{base}/remove_user/", members - desired, "remove")

    if added or removed:
        logger.info(
            "authentik group %r: %d added, %d removed", group_name, added, removed
        )
    return (added, removed)
