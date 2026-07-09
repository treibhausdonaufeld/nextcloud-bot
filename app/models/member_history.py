"""Member history: tracks when a person joined or left a role in a group."""

import time
from typing import ClassVar

import edgy

from app.models.base import BaseDBModel


class MemberHistory(BaseDBModel):
    group_page_id: int = edgy.BigIntegerField()
    group_name: str = edgy.CharField(max_length=255, default="")
    username: str = edgy.CharField(max_length=255)
    role: str = edgy.CharField(max_length=32)
    first_seen: float = edgy.FloatField()
    last_seen: float = edgy.FloatField()
    active: bool = edgy.BooleanField(default=True)

    natural_key_fields: ClassVar[tuple[str, ...]] = (
        "group_page_id",
        "username",
        "role",
    )

    class Meta:
        tablename = "member_history"

    @classmethod
    def record_changes(
        cls,
        group_page_id: int,
        group_name: str,
        *,
        coordination: list[str],
        delegate: list[str],
        members: list[str],
        absent: list[str],
    ) -> None:
        """Update history for a group based on current member lists.

        - Members still in a role → bump ``last_seen``.
        - Members who left a role → mark ``active = False``.
        - New members in a role → create a history row.
        """
        now = time.time()

        current = (
            {(username, "coordination") for username in coordination}
            | {(username, "delegate") for username in delegate}
            | {(username, "member") for username in members}
            | {(username, "absent") for username in absent}
        )

        existing_rows: list[MemberHistory] = cls.fetch(
            group_page_id=group_page_id, active=True, limit=10000
        )
        existing_active = {row.natural_key_tuple: row for row in existing_rows}

        for (_page_id, username, role), row in existing_active.items():
            if (username, role) in current:
                row.last_seen = now
                row.store()
            else:
                row.last_seen = now
                row.active = False
                row.store()

        for username, role in current:
            if (username, role) in existing_active:
                continue
            existing = cls.fetch_one(
                group_page_id=group_page_id, username=username, role=role
            )
            if existing is not None:
                existing.last_seen = now
                existing.active = True
                existing.store()
            else:
                cls(
                    group_page_id=group_page_id,
                    group_name=group_name,
                    username=username,
                    role=role,
                    first_seen=now,
                    last_seen=now,
                    active=True,
                ).store()

    @property
    def natural_key_tuple(self) -> tuple[int, str, str]:
        return (self.group_page_id, self.username, self.role)
