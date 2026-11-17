"""Read-only view of the current organisation for external tools.

The external API intentionally exposes only the *present*: the active `Group`
rows (which mirror the current wiki pages) and the usernames they list today.
The `GroupRole` and `MemberLeave` history tables are never consulted, and
retired groups keep their last known membership internally but are left out
here.

Two shapes are built from the same data, so an external tool can pick the
direction that suits it:

- :func:`groups_payload` groups the members by group (and carries the parent
  group of every group);
- :func:`members_payload` groups the groups by member (and includes members
  without any current group).
"""

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import Group, NCUserList
from app.models.group_role import ROLE_FIELDS, ROLES

RoleName = Literal["coordination", "delegate", "member"]


class GroupMembership(BaseModel):
    """The role a member holds in one group."""

    username: str = Field(description="Nextcloud uid (the Nextcloud username)")
    authentik_username: str = Field(
        default="",
        description="authentik username/handle, when known (falls back to an empty string)",
    )
    displayname: str = Field(
        description="Human readable name, falling back to the username"
    )
    role: RoleName = Field(
        description="Role in this group: `coordination`, `delegate` or `member`"
    )


class GroupWithMembers(BaseModel):
    name: str
    parent_group: str | None = Field(
        default=None,
        description="Name of the parent group, or null for a top-level group",
    )
    members: list[GroupMembership]


class GroupsResponse(BaseModel):
    generated_at: str = Field(description="ISO 8601 timestamp of this snapshot (UTC)")
    groups: list[GroupWithMembers]


class MemberGroup(BaseModel):
    name: str
    parent_group: str | None = Field(
        default=None,
        description="Name of the parent group, or null for a top-level group",
    )
    role: RoleName = Field(
        description="Role the member holds in this group: `coordination`, `delegate` or `member`"
    )


class MemberWithGroups(BaseModel):
    username: str = Field(description="Nextcloud uid (the Nextcloud username)")
    authentik_username: str = Field(
        default="",
        description="authentik username/handle, when known (falls back to an empty string)",
    )
    displayname: str = Field(
        description="Human readable name, falling back to the username"
    )
    groups: list[MemberGroup]


class MembersResponse(BaseModel):
    generated_at: str = Field(description="ISO 8601 timestamp of this snapshot (UTC)")
    members: list[MemberWithGroups]


def _snapshot_time() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _active_groups() -> list[Group]:
    """Current groups, ordered by name (retired groups are not exported)."""
    return sorted(
        Group.active_cached(), key=lambda group: (group.name.casefold(), group.name)
    )


def _user_fields(user_list: NCUserList, username: str) -> dict[str, str]:
    user = user_list.get_user_by_uid(username)
    return {
        "username": username,
        "authentik_username": (user.authentik_username if user else "") or "",
        "displayname": (user.displayname if user else "") or username,
    }


def _assignments(group: Group) -> list[tuple[str, str]]:
    """Current (username, role) pairs of a group, deduplicated by seniority.

    The parsed group fields are already disjoint (members excludes
    coordination/delegates), but an old or hand-edited row could name somebody
    twice. Keeping the first hit means the more senior role wins.
    """
    seen: dict[str, str] = {}
    for role in ROLES:
        field = ROLE_FIELDS[role]
        for username in getattr(group, field, []) or []:
            seen.setdefault(username, role)
    return list(seen.items())


def groups_payload() -> dict[str, Any]:
    """All active groups with the members and roles they currently have."""
    user_list = NCUserList()
    groups: list[dict[str, Any]] = []

    for group in _active_groups():
        members = [
            {**_user_fields(user_list, username), "role": role}
            for username, role in _assignments(group)
        ]
        members.sort(
            key=lambda member: (
                ROLES.index(member["role"]),
                member["displayname"].casefold(),
                member["username"],
            )
        )
        groups.append(
            {
                "name": group.name,
                "parent_group": group.parent_group or None,
                "members": members,
            }
        )

    return {"generated_at": _snapshot_time(), "groups": groups}


def members_payload() -> dict[str, Any]:
    """All current members with the groups and roles they currently hold.

    Members without any group are included with an empty list, and so is
    anybody who still appears on a current group page even if they are not in
    the configured authentik member group (e.g. an external delegate) — the
    two API views then always describe the same membership.
    """
    user_list = NCUserList()
    assignments: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for group in _active_groups():
        for username, role in _assignments(group):
            assignments[username].append(
                {
                    "name": group.name,
                    "parent_group": group.parent_group or None,
                    "role": role,
                }
            )

    current_members = {user.username for user in user_list.get_member_users()}
    usernames = current_members | set(assignments)

    rows: list[dict[str, Any]] = []
    for username in usernames:
        user = _user_fields(user_list, username)
        groups = sorted(
            assignments.get(username, []),
            key=lambda entry: (entry["name"].casefold(), entry["name"]),
        )
        rows.append({**user, "groups": groups})

    rows.sort(key=lambda row: (row["displayname"].casefold(), row["username"]))
    return {"generated_at": _snapshot_time(), "members": rows}
