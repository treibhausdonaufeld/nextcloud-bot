"""Role history: who held which role in which group, and when.

`Group` only ever stores the *current* membership of a group page. To answer
"who was coordinator of AG Haus between 2025-01-01 and 2026-02-28", every
observed membership is additionally recorded here as a row with a start and
(once the person leaves the role) an end timestamp. Rows with `end_date is
None` describe the role someone holds right now.

The history is built up during the regular sync: `sync_group()` compares the
freshly parsed group against the open rows and closes/opens rows accordingly.
Timestamps come from the group page's Nextcloud modification time, so a change
is dated when the page was edited rather than when the bot happened to see it.
Consequently the very first sync dates all existing roles to the group page's
last modification — earlier history simply predates the bot's tracking.
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import edgy

from app.models.base import BaseDBModel, format_date

if TYPE_CHECKING:
    from app.models.group import Group

logger = logging.getLogger(__name__)

ROLE_COORDINATION = "coordination"
ROLE_DELEGATE = "delegate"
ROLE_MEMBER = "member"

# Role -> attribute holding the usernames on `Group`. Ordered by seniority;
# templates and overviews follow this order.
ROLE_FIELDS: Dict[str, str] = {
    ROLE_COORDINATION: "coordination",
    ROLE_DELEGATE: "delegate",
    ROLE_MEMBER: "members",
}
ROLES: Tuple[str, ...] = tuple(ROLE_FIELDS)


class GroupRole(BaseDBModel):
    username: str = edgy.CharField(max_length=255, index=True)
    group_name: str = edgy.CharField(max_length=255, index=True)
    # page id of the group page; stable across group renames
    page_id: int = edgy.BigIntegerField(index=True)
    role: str = edgy.CharField(max_length=32, index=True)

    # unix timestamps; end_date is None while the role is held
    start_date: int = edgy.BigIntegerField(index=True)
    end_date: int | None = edgy.BigIntegerField(null=True, index=True)

    class Meta:
        tablename = "group_roles"

    def __str__(self) -> str:
        return (
            f"GroupRole({self.username} {self.role} in {self.group_name}"
            f" {self.start_display}-{self.end_display or ''})"
        )

    @property
    def is_current(self) -> bool:
        return self.end_date is None

    @property
    def start_display(self) -> str:
        return format_date(self.start_date) or ""

    @property
    def end_display(self) -> str | None:
        return format_date(self.end_date)

    # --- queries ------------------------------------------------------------

    @classmethod
    def for_group_page(cls, page_id: int) -> List["GroupRole"]:
        return cls.fetch(page_id=page_id, limit=10000, order_by="-start_date")

    @classmethod
    def for_user(cls, username: str) -> List["GroupRole"]:
        """All role assignments of one user, newest first."""
        return cls.fetch(username=username, limit=10000, order_by="-start_date")

    @classmethod
    def for_role(cls, role: str, group_name: str = "") -> List["GroupRole"]:
        """All assignments of one role, optionally limited to one group."""
        if group_name:
            return cls.fetch(
                role=role,
                group_name=group_name,
                limit=10000,
                order_by="-start_date",
            )
        return cls.fetch(role=role, limit=10000, order_by="-start_date")

    @classmethod
    def current(cls) -> List["GroupRole"]:
        """All currently held roles."""
        return cls.fetch(limit=10000, order_by="-start_date", end_date__isnull=True)

    @classmethod
    def all_rows(cls) -> List["GroupRole"]:
        return cls.fetch(limit=100000, order_by="-start_date")

    # --- history bookkeeping ------------------------------------------------

    @classmethod
    def sync_group(cls, group: "Group", timestamp: int | None = None) -> None:
        """Reconcile the stored history with the group's current membership.

        Roles that vanished from the page are closed, new ones are opened. A
        role that reappears after having been closed at (or after) the same
        timestamp is reopened instead of duplicated, so re-parsing all pages
        does not fragment the history.
        """
        moment = timestamp or int(datetime.now().timestamp())

        desired = {
            (username, role)
            for role, field in ROLE_FIELDS.items()
            for username in getattr(group, field, []) or []
        }

        rows = cls.for_group_page(group.page_id)

        # Group renames keep the page id, so carry the new name over.
        for row in rows:
            if row.group_name != group.name:
                row.group_name = group.name
                row.store()

        open_rows = {(r.username, r.role): r for r in rows if r.end_date is None}
        for key, row in open_rows.items():
            if key in desired:
                continue
            row.end_date = max(moment, row.start_date)
            row.store()
            logger.info("Ended role %s", row)

        latest_closed: Dict[Tuple[str, str], GroupRole] = {}
        for row in rows:
            if row.end_date is None:
                continue
            key = (row.username, row.role)
            known = latest_closed.get(key)
            if known is None or (row.end_date or 0) > (known.end_date or 0):
                latest_closed[key] = row

        for username, role in sorted(desired - set(open_rows)):
            reusable = latest_closed.get((username, role))
            if reusable is not None and (reusable.end_date or 0) >= moment:
                # The role never actually ended (e.g. a full re-parse closed
                # it a moment ago) — reopen the existing row.
                reusable.end_date = None
                reusable.store()
                continue
            row = cls(
                username=username,
                group_name=group.name,
                page_id=group.page_id,
                role=role,
                start_date=moment,
            )
            row.store()
            logger.info("Started role %s", row)

    @classmethod
    def close_for_page(cls, page_id: int, timestamp: int | None = None) -> None:
        """End all open roles of a group page (the group page is gone)."""
        moment = timestamp or int(datetime.now().timestamp())
        for row in cls.for_group_page(page_id):
            if row.end_date is not None:
                continue
            row.end_date = max(moment, row.start_date)
            row.store()

    @classmethod
    def start_dates_by_key(cls) -> Dict[Tuple[int, str, str], int]:
        """`(page_id, username, role) -> start timestamp` for open roles."""
        return {(r.page_id, r.username, r.role): r.start_date for r in cls.current()}

    @classmethod
    def latest_for(
        cls, page_id: int, username: str, role: str
    ) -> Optional["GroupRole"]:
        rows = cls.fetch(
            page_id=page_id,
            username=username,
            role=role,
            order_by="-start_date",
            limit=1,
        )
        return rows[0] if rows else None
