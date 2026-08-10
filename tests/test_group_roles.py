"""Unit tests for the role history bookkeeping (`GroupRole.sync_group`)."""

from unittest.mock import patch

import pytest

from app.models.group import Group
from app.models.group_role import GroupRole

PARSER = "app.services.collectives_parser"

JAN = 1735689600  # 2025-01-01
FEB = 1738368000  # 2025-02-01
MAR = 1740787200  # 2025-03-01


@pytest.fixture
def group():
    return Group(
        name="AG Haus",
        page_id=42,
        coordination=["alice"],
        delegate=[],
        members=["bob"],
    )


def sync(group, rows, timestamp):
    """Run sync_group against a fixed set of stored rows, capturing stores."""
    stored = []
    with patch.object(GroupRole, "for_group_page", return_value=rows):
        with patch.object(GroupRole, "store", autospec=True) as store:
            store.side_effect = lambda self, **kwargs: stored.append(self)
            GroupRole.sync_group(group, timestamp=timestamp)
    return stored


def role(username, name="AG Haus", page_id=42, role="member", start=JAN, end=None):
    return GroupRole(
        username=username,
        group_name=name,
        page_id=page_id,
        role=role,
        start_date=start,
        end_date=end,
    )


class TestSyncGroupOpensRoles:
    def test_new_group_records_every_role(self, group):
        stored = sync(group, [], JAN)

        recorded = {(r.username, r.role, r.start_date, r.end_date) for r in stored}
        assert recorded == {
            ("alice", "coordination", JAN, None),
            ("bob", "member", JAN, None),
        }

    def test_existing_roles_are_not_touched(self, group):
        rows = [
            role("alice", role="coordination"),
            role("bob", role="member"),
        ]

        assert sync(group, rows, FEB) == []

    def test_new_member_gets_own_row(self, group):
        rows = [role("alice", role="coordination"), role("bob", role="member")]
        group.members = ["bob", "carol"]

        stored = sync(group, rows, FEB)

        assert len(stored) == 1
        assert (stored[0].username, stored[0].role) == ("carol", "member")
        assert stored[0].start_date == FEB
        assert stored[0].end_date is None


class TestSyncGroupClosesRoles:
    def test_departed_member_is_ended(self, group):
        rows = [role("alice", role="coordination"), role("bob", role="member")]
        group.members = []

        stored = sync(group, rows, FEB)

        assert len(stored) == 1
        assert stored[0].username == "bob"
        assert stored[0].end_date == FEB

    def test_role_change_closes_old_and_opens_new(self, group):
        rows = [role("alice", role="coordination"), role("bob", role="member")]
        group.coordination = ["alice", "bob"]
        group.members = []

        stored = sync(group, rows, FEB)

        ended = [r for r in stored if r.end_date is not None]
        started = [r for r in stored if r.end_date is None]
        assert [(r.username, r.role, r.end_date) for r in ended] == [
            ("bob", "member", FEB)
        ]
        assert [(r.username, r.role, r.start_date) for r in started] == [
            ("bob", "coordination", FEB)
        ]

    def test_end_date_never_precedes_start_date(self, group):
        # A page edited before the role was first seen must not produce a
        # negative-length assignment.
        rows = [role("alice", role="coordination"), role("bob", start=MAR)]
        group.members = []

        stored = sync(group, rows, FEB)

        assert stored[0].end_date == MAR

    def test_close_for_page_ends_open_rows_only(self):
        rows = [role("alice", role="coordination"), role("bob", end=FEB)]
        stored = []
        with patch.object(GroupRole, "for_group_page", return_value=rows):
            with patch.object(GroupRole, "store", autospec=True) as store:
                store.side_effect = lambda self, **kwargs: stored.append(self)
                GroupRole.close_for_page(42, timestamp=MAR)

        assert [(r.username, r.end_date) for r in stored] == [("alice", MAR)]


class TestSyncGroupIsIdempotent:
    def test_recently_closed_role_is_reopened_instead_of_duplicated(self, group):
        # A full re-parse (`clear-parsed-data`) closes every role and then
        # sees the same people again; the history must not fragment.
        rows = [
            role("alice", role="coordination", end=MAR),
            role("bob", role="member", end=MAR),
        ]

        stored = sync(group, rows, FEB)

        assert len(stored) == 2
        assert all(r.end_date is None for r in stored)
        assert all(r.start_date == JAN for r in stored)

    def test_returning_member_gets_a_new_period(self, group):
        rows = [
            role("alice", role="coordination"),
            role("bob", role="member", end=FEB),
        ]

        stored = sync(group, rows, MAR)

        assert len(stored) == 1
        assert stored[0].start_date == MAR
        assert stored[0].end_date is None
        # the old period is untouched
        assert rows[1].end_date == FEB


class TestSyncGroupRename:
    def test_group_rename_updates_history_rows(self, group):
        rows = [role("alice", name="AG Altbau", role="coordination")]
        group.members = []

        stored = sync(group, rows, FEB)

        assert rows[0].group_name == "AG Haus"
        assert rows[0] in stored


class TestBackfill:
    """Groups parsed before the history existed get seeded once."""

    def test_seeds_groups_without_history_from_the_page_timestamp(self, group):
        from app.models.collective_page import CollectivePage
        from app.services.collectives_parser import backfill_role_history

        page = CollectivePage(page_id=42, title="AG Haus", timestamp=MAR)
        stored = []
        with (
            patch(PARSER + ".get_state", return_value=None),
            patch(PARSER + ".set_state"),
            patch.object(GroupRole, "all_rows", return_value=[]),
            patch.object(Group, "fetch", return_value=[group]),
            patch.object(CollectivePage, "get_from_page_id_or_none", return_value=page),
            patch.object(GroupRole, "for_group_page", return_value=[]),
            patch.object(GroupRole, "store", autospec=True) as store,
        ):
            store.side_effect = lambda self, **kw: stored.append(self)
            backfill_role_history()

        assert {(r.username, r.start_date) for r in stored} == {
            ("alice", MAR),
            ("bob", MAR),
        }

    def test_groups_with_history_are_skipped(self, group):
        from app.services.collectives_parser import backfill_role_history

        with (
            patch(PARSER + ".get_state", return_value=None),
            patch(PARSER + ".set_state"),
            patch.object(GroupRole, "all_rows", return_value=[role("alice")]),
            patch.object(Group, "fetch", return_value=[group]),
            patch.object(GroupRole, "sync_group") as sync_group,
        ):
            backfill_role_history()

        sync_group.assert_not_called()

    def test_backfill_is_skipped_once_it_has_run(self):
        from app.services.collectives_parser import backfill_role_history

        with (
            patch(PARSER + ".get_state", return_value={"done": True}),
            patch.object(GroupRole, "all_rows") as all_rows,
        ):
            backfill_role_history()

        all_rows.assert_not_called()

    def test_completion_is_persisted(self, group):
        from app.services.collectives_parser import (
            ROLE_BACKFILL_STATE_KEY,
            backfill_role_history,
        )

        with (
            patch(PARSER + ".get_state", return_value=None),
            patch(PARSER + ".set_state") as set_state,
            patch.object(GroupRole, "all_rows", return_value=[role("alice")]),
            patch.object(Group, "fetch", return_value=[group]),
        ):
            backfill_role_history()

        set_state.assert_called_once_with(ROLE_BACKFILL_STATE_KEY, {"done": True})


class TestDisplayHelpers:
    def test_dates_are_rendered_as_iso_dates(self):
        assignment = role("alice", start=JAN, end=MAR)

        assert assignment.start_display == "2025-01-01"
        assert assignment.end_display == "2025-03-01"
        assert assignment.is_current is False

    def test_open_assignment_has_no_end(self):
        assert role("alice").end_display is None
        assert role("alice").is_current is True
