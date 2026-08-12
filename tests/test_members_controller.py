"""Unit tests for the members overview and its detail views."""

from unittest.mock import patch

import pytest

from app.controllers import members as members_controller
from app.models.group import Group
from app.models.group_role import GroupRole
from app.models.user import NCUser
from app.settings import settings

JAN = 1735689600  # 2025-01-01
FEB = 1738368000  # 2025-02-01
MAR = 1740787200  # 2025-03-01


@pytest.fixture
def groups():
    return [
        Group(
            name="AG Haus",
            page_id=1,
            coordination=["alice"],
            delegate=[],
            members=["bob"],
        ),
        Group(
            name="AG Garten",
            page_id=2,
            coordination=[],
            delegate=["bob"],
            members=[],
        ),
    ]


@pytest.fixture
def history():
    return [
        GroupRole(
            username="alice",
            group_name="AG Haus",
            page_id=1,
            role="coordination",
            start_date=JAN,
        ),
        GroupRole(
            username="bob",
            group_name="AG Haus",
            page_id=1,
            role="member",
            start_date=FEB,
        ),
        GroupRole(
            username="bob",
            group_name="AG Garten",
            page_id=2,
            role="delegate",
            start_date=FEB,
        ),
        # carol left the coordination of AG Haus before alice took over
        GroupRole(
            username="carol",
            group_name="AG Haus",
            page_id=1,
            role="coordination",
            start_date=JAN,
            end_date=MAR,
        ),
    ]


@pytest.fixture
def users():
    return {
        "alice": NCUser(
            username="alice",
            displayname="Alice Anders",
            enabled=True,
            authentik_groups=["Mitglieder"],
        ),
        "bob": NCUser(
            username="bob",
            displayname="Bob Berger",
            enabled=True,
            authentik_groups=[],
        ),
        # carol is no longer an active Nextcloud user
        "carol": NCUser(username="carol", displayname="Carol Curie", enabled=False),
    }


@pytest.fixture(autouse=True)
def patched_data(groups, history, users):
    from app.models.user import NCUserList

    NCUserList._cached_users = users
    with patch.object(Group, "all_cached", return_value=groups):
        with patch.object(GroupRole, "all_rows", return_value=history):
            with patch.object(
                GroupRole,
                "current",
                return_value=[r for r in history if r.end_date is None],
            ):
                yield


class TestMemberRows:
    def test_lists_current_roles_per_member(self):
        rows = {row["username"]: row for row in members_controller.member_rows()}

        assert set(rows) == {"alice", "bob", "carol"}
        assert [(r["group"], r["role"]) for r in rows["bob"]["roles"]] == [
            ("AG Garten", "delegate"),
            ("AG Haus", "member"),
        ]
        assert rows["alice"]["roles"][0]["role_label"]
        assert rows["alice"]["roles"][0]["start"] == "2025-01-01"

    def test_former_members_are_listed_as_inactive_without_roles(self):
        rows = {row["username"]: row for row in members_controller.member_rows()}

        assert rows["carol"]["active"] is False
        assert rows["carol"]["roles"] == []
        assert rows["carol"]["past_count"] == 1
        assert rows["alice"]["past_count"] == 0

    def test_role_filter_keeps_only_holders_of_that_role(self):
        rows = members_controller.member_rows(role_filter="coordination")

        assert [row["username"] for row in rows] == ["alice"]

    def test_group_filter_keeps_only_current_members_of_that_group(self):
        rows = members_controller.member_rows(group_filter="AG Garten")

        assert [row["username"] for row in rows] == ["bob"]

    def test_query_matches_the_display_name(self):
        rows = members_controller.member_rows(query="berger")

        assert [row["username"] for row in rows] == ["bob"]

    def test_active_members_are_sorted_before_inactive_ones(self):
        rows = members_controller.member_rows()

        assert [row["username"] for row in rows] == ["alice", "bob", "carol"]

    def test_only_the_configured_authentik_group_is_listed(self):
        # With AUTH__MEMBER_GROUP_NAME set (and authentik connected), the page
        # lists members of that group only — bob and carol drop out even
        # though they hold roles or appear in the history.
        with (
            patch.object(settings.auth, "member_group_name", "Mitglieder"),
            patch.object(settings.auth, "authentik_base_url", "https://auth.test"),
        ):
            rows = members_controller.member_rows()

        assert [row["username"] for row in rows] == ["alice"]
        assert rows[0]["active"] is True


class TestMemberHistory:
    def test_splits_current_and_past_roles(self, history):
        with patch.object(GroupRole, "for_user", return_value=history[3:]):
            current, past = members_controller.member_history("carol")

        assert current == []
        assert past[0]["group"] == "AG Haus"
        assert past[0]["start"] == "2025-01-01"
        assert past[0]["end"] == "2025-03-01"

    def test_current_roles_have_no_end_date(self, history):
        with patch.object(GroupRole, "for_user", return_value=history[:1]):
            current, past = members_controller.member_history("alice")

        assert past == []
        assert current[0]["end"] is None
        assert current[0]["role"] == "coordination"


class TestRoleHolders:
    def test_lists_current_and_previous_holders(self, history):
        rows = [r for r in history if r.role == "coordination"]
        with patch.object(GroupRole, "for_role", return_value=rows):
            current, past = members_controller.role_holders("coordination", "AG Haus")

        assert [h["displayname"] for h in current] == ["Alice Anders"]
        assert [h["displayname"] for h in past] == ["Carol Curie"]
        assert past[0]["end"] == "2025-03-01"

    def test_unknown_users_fall_back_to_the_username(self, history):
        row = GroupRole(
            username="dave",
            group_name="AG Haus",
            page_id=1,
            role="member",
            start_date=JAN,
        )
        with patch.object(GroupRole, "for_role", return_value=[row]):
            current, _ = members_controller.role_holders("member")

        assert current[0]["displayname"] == "dave"
