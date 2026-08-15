"""Unit tests for the global leave ("Karenz") status.

Covers both halves: pulling the marker out of a group page's markdown, and
reconciling the stored history against what the pages say.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from app.models.collective_page import CollectivePage
from app.models.group import Group
from app.models.member_leave import MemberLeave, end_of_day, parse_until
from app.services.config import OrganisationConfig

# 2025-08-12 and 2026-09-20; the leaves below end in between.
NOW = 1755000000
LATER = 1790000000


@pytest.fixture
def organisation():
    return OrganisationConfig()


@pytest.fixture
def mock_bot_config(organisation):
    config = MagicMock()
    config.organisation = organisation
    return config


@pytest.fixture
def mock_page():
    page = Mock(spec=CollectivePage)
    page.content = ""
    page.full_path = "AG Test"
    page.file_path = "AG Test/README.md"
    page.page_id = 1
    page.emoji = ""
    return page


def parse(content: str, page, config) -> Group:
    group = Group(name="AG Test", page_id=page.page_id)
    page.content = content
    with (
        patch("app.models.group.bot_config", config),
        patch.object(CollectivePage, "get_from_page_id", return_value=page),
        patch.object(Group, "store"),
    ):
        group.update_from_page()
    return group


class TestLeaveParsing:
    def test_inline_marker_keeps_the_membership(self, mock_page, mock_bot_config):
        group = parse(
            """
**Mitglieder:**
- mention://user/alice
- mention://user/bob (Karenz bis 30.06.2026)
""",
            mock_page,
            mock_bot_config,
        )

        assert group.members == ["alice", "bob"]
        assert group.on_leave == ["bob"]
        assert group.leave_until == {"bob": end_of_day(2026, 6, 30)}

    def test_a_leave_section_does_not_add_members(self, mock_page, mock_bot_config):
        group = parse(
            """
**Mitglieder:** mention://user/alice

**Karenz:**
- mention://user/bob
- mention://user/carol bis 2026-01-31
""",
            mock_page,
            mock_bot_config,
        )

        assert group.members == ["alice"]
        assert group.on_leave == ["bob", "carol"]
        # bob has no date of his own, so his leave is open-ended
        assert group.leave_until == {"carol": end_of_day(2026, 1, 31)}

    def test_a_section_heading_dates_the_names_below_it(
        self, mock_page, mock_bot_config
    ):
        group = parse(
            """
**Karenz bis 30.06.2026:**
- mention://user/bob
- mention://user/carol
""",
            mock_page,
            mock_bot_config,
        )

        until = end_of_day(2026, 6, 30)
        assert group.leave_until == {"bob": until, "carol": until}

    def test_an_open_ended_marker_wins_over_a_dated_one(
        self, mock_page, mock_bot_config
    ):
        group = parse(
            """
**Karenz:** mention://user/bob

**Mitglieder:** mention://user/bob (Karenz bis 30.06.2026)
""",
            mock_page,
            mock_bot_config,
        )

        assert group.on_leave == ["bob"]
        assert group.leave_until == {}

    def test_pages_without_a_marker_stay_empty(self, mock_page, mock_bot_config):
        group = parse(
            """
**Koordination:** mention://user/alice

**Mitglieder:** mention://user/bob
""",
            mock_page,
            mock_bot_config,
        )

        assert group.on_leave == []
        assert group.leave_until == {}
        assert group.members == ["bob"]

    def test_the_section_ends_at_the_next_heading(self, mock_page, mock_bot_config):
        group = parse(
            """
**Karenz:** mention://user/bob

## Sonstiges

mention://user/carol
""",
            mock_page,
            mock_bot_config,
        )

        assert group.on_leave == ["bob"]


class TestParseUntil:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Karenz bis 30.06.2026", (2026, 6, 30)),
            ("Karenz bis 2026-06-30", (2026, 6, 30)),
            ("on leave until 2026-06-30", (2026, 6, 30)),
            ("Karenz bis 1.7.2026", (2026, 7, 1)),
        ],
    )
    def test_reads_the_announced_end(self, text, expected, organisation):
        assert parse_until(text, organisation.leave_until_keywords) == end_of_day(
            *expected
        )

    def test_a_start_date_is_not_an_end_date(self, organisation):
        assert (
            parse_until("Karenz seit 01.01.2026", organisation.leave_until_keywords)
            is None
        )

    def test_no_date_means_open_ended(self, organisation):
        assert parse_until("Karenz", organisation.leave_until_keywords) is None

    def test_impossible_dates_are_ignored(self, organisation):
        assert (
            parse_until("Karenz bis 31.02.2026", organisation.leave_until_keywords)
            is None
        )


class TestSyncGroups:
    """`MemberLeave.sync_groups` reconciles the history with the wiki."""

    @pytest.fixture
    def store(self):
        rows: list[MemberLeave] = []

        def fake_store(self, *args, **kwargs):
            if all(row is not self for row in rows):
                rows.append(self)

        with (
            patch.object(MemberLeave, "store", fake_store),
            patch.object(MemberLeave, "all_rows", lambda: list(rows)),
            patch.object(
                MemberLeave,
                "open_rows",
                lambda: [row for row in rows if row.end_date is None],
            ),
        ):
            yield rows

    def group(self, on_leave, leave_until=None):
        return Group(
            name="AG Test",
            page_id=1,
            on_leave=on_leave,
            leave_until=leave_until or {},
        )

    def test_opens_a_row_dated_by_the_page(self, store):
        group = self.group(["bob"])
        MemberLeave.sync_groups([group], timestamps={1: NOW}, now=NOW)

        assert len(store) == 1
        assert store[0].username == "bob"
        assert store[0].start_date == NOW
        assert store[0].is_current(NOW)

    def test_reparsing_does_not_duplicate_a_running_leave(self, store):
        group = self.group(["bob"])
        for _ in range(3):
            MemberLeave.sync_groups([group], timestamps={1: NOW}, now=NOW)

        assert len(store) == 1

    def test_removing_the_marker_ends_the_leave(self, store):
        MemberLeave.sync_groups([self.group(["bob"])], timestamps={1: NOW}, now=NOW)
        MemberLeave.sync_groups([self.group([])], timestamps={1: NOW}, now=LATER)

        assert store[0].end_date == LATER
        assert not store[0].is_current(LATER)

    def test_the_announced_end_closes_the_leave_on_its_own(self, store):
        until = end_of_day(2026, 6, 30)
        group = self.group(["bob"], {"bob": until})
        MemberLeave.sync_groups([group], timestamps={1: NOW}, now=NOW)

        assert store[0].is_current(NOW)

        # Nobody touched the page, but the date has passed.
        MemberLeave.sync_groups([group], timestamps={1: NOW}, now=LATER)

        assert store[0].end_date == until
        assert not store[0].is_current(LATER)

    def test_a_marker_left_behind_does_not_restart_the_leave(self, store):
        group = self.group(["bob"], {"bob": end_of_day(2026, 6, 30)})
        MemberLeave.sync_groups([group], timestamps={1: NOW}, now=NOW)
        MemberLeave.sync_groups([group], timestamps={1: NOW}, now=LATER)
        MemberLeave.sync_groups([group], timestamps={1: NOW}, now=LATER)

        assert len(store) == 1

    def test_a_new_leave_after_an_old_one_gets_its_own_row(self, store):
        MemberLeave.sync_groups(
            [self.group(["bob"], {"bob": end_of_day(2026, 6, 30)})],
            timestamps={1: NOW},
            now=NOW,
        )
        MemberLeave.sync_groups([self.group([])], timestamps={1: NOW}, now=LATER)
        MemberLeave.sync_groups([self.group(["bob"])], timestamps={1: LATER}, now=LATER)

        assert len(store) == 2
        assert MemberLeave.current_by_user(LATER)["bob"].start_date == LATER

    def test_an_extended_leave_keeps_its_start(self, store):
        group = self.group(["bob"], {"bob": end_of_day(2026, 6, 30)})
        MemberLeave.sync_groups([group], timestamps={1: NOW}, now=NOW)

        extended = end_of_day(2027, 6, 30)
        MemberLeave.sync_groups(
            [self.group(["bob"], {"bob": extended})], timestamps={1: NOW}, now=NOW
        )

        assert len(store) == 1
        assert store[0].start_date == NOW
        assert store[0].until_date == extended

    def test_the_status_survives_being_dropped_from_one_page(self, store):
        marked = Group(name="AG Haus", page_id=1, on_leave=["bob"])
        other = Group(name="AG Garten", page_id=2, on_leave=["bob"])
        MemberLeave.sync_groups([marked, other], timestamps={1: NOW, 2: NOW}, now=NOW)

        # AG Garten drops the marker, AG Haus keeps it.
        MemberLeave.sync_groups(
            [marked, Group(name="AG Garten", page_id=2, on_leave=[])],
            timestamps={1: NOW, 2: NOW},
            now=LATER,
        )

        assert len(store) == 1
        assert store[0].is_current(LATER)

    def test_a_group_that_is_gone_stops_marking_anybody(self, store):
        MemberLeave.sync_groups([self.group(["bob"])], timestamps={1: NOW}, now=NOW)
        MemberLeave.sync_groups([], timestamps={}, now=LATER)

        assert not store[0].is_current(LATER)
