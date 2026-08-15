"""Unit tests for the group detail dialog opened from the org chart."""

from unittest.mock import patch

import pytest

from app.controllers import groups as groups_controller
from app.models.group import Group
from app.models.member_leave import MemberLeave
from app.models.user import NCUser, NCUserList

FEB = 1738368000  # 2025-02-01
FAR_FUTURE = 1893456000  # 2030-01-01


@pytest.fixture
def group():
    return Group(
        name="AG Haus",
        page_id=1,
        coordination=["alice"],
        delegate=["bob"],
        members=["dave", "carol"],
    )


@pytest.fixture(autouse=True)
def users():
    NCUserList._cached_users = {
        "alice": NCUser(username="alice", displayname="Alice Anders", enabled=True),
        "bob": NCUser(username="bob", displayname="Bob Berger", enabled=True),
        "carol": NCUser(username="carol", displayname="Carol Curie", enabled=True),
        "dave": NCUser(username="dave", displayname="Dave Dorn", enabled=True),
    }
    yield


@pytest.fixture
def no_leaves():
    with patch.object(MemberLeave, "open_rows", return_value=[]):
        yield


def rows(group):
    return groups_controller.group_member_rows(group, NCUserList())


class TestGroupMemberRows:
    def test_reports_the_role_each_member_holds(self, group, no_leaves):
        by_user = {row["username"]: row for row in rows(group)}

        assert by_user["alice"]["role"] == "coordination"
        assert by_user["bob"]["role"] == "delegate"
        assert by_user["carol"]["role"] == "member"
        assert by_user["alice"]["role_label"]

    def test_lists_every_member_once_sorted_by_seniority(self, group, no_leaves):
        assert [row["username"] for row in rows(group)] == [
            "alice",  # coordination
            "bob",  # delegate
            "carol",  # members, by display name
            "dave",
        ]

    def test_all_badges_share_the_group_hue(self, group, no_leaves):
        hues = {row["hue"] for row in rows(group)}

        assert len(hues) == 1
        assert hues.pop() == groups_controller.group_hue("AG Haus")

    def test_unknown_users_fall_back_to_the_username(self, no_leaves):
        group = Group(name="AG Haus", page_id=1, members=["nobody"])

        assert rows(group)[0]["displayname"] == "nobody"

    def test_a_leave_from_another_group_still_marks_the_member(self, group):
        # The status is global: bob is marked on AG Garten's page.
        leave = MemberLeave(
            username="bob",
            group_name="AG Garten",
            page_id=9,
            start_date=FEB,
            until_date=FAR_FUTURE,
        )
        with patch.object(MemberLeave, "open_rows", return_value=[leave]):
            by_user = {row["username"]: row for row in rows(group)}

        assert by_user["bob"]["on_leave"] is True
        assert by_user["bob"]["leave_until"] == "2030-01-01"
        assert by_user["alice"]["on_leave"] is False

    def test_an_expired_leave_does_not_mark_anybody(self, group):
        leave = MemberLeave(
            username="bob",
            group_name="AG Haus",
            page_id=1,
            start_date=FEB,
            until_date=FEB,
        )
        with patch.object(MemberLeave, "open_rows", return_value=[leave]):
            by_user = {row["username"]: row for row in rows(group)}

        assert by_user["bob"]["on_leave"] is False

    def test_empty_groups_produce_no_rows(self, no_leaves):
        assert rows(Group(name="AG Leer", page_id=2)) == []


class TestRoleBadgeMacro:
    """The badge is one pill with two destinations."""

    @staticmethod
    def render(group="RT Freiraum", role="member", label="Mitglied", extra=""):
        from jinja2 import Environment, FileSystemLoader

        env = Environment(loader=FileSystemLoader("app/templates"))
        macro = env.get_template("partials/role_badge.html").module.role_badge
        return str(macro(group, role, label, 200, extra))

    def test_the_group_half_opens_the_group_details(self):
        html = self.render()

        assert 'hx-get="/groups/detail?node=RT%20Freiraum"' in html
        assert ">RT Freiraum</a" in html

    def test_the_group_half_keeps_a_real_href_for_new_tabs(self):
        assert 'href="/groups?limit_group=RT%20Freiraum"' in self.render()

    def test_the_role_half_opens_the_role_details(self):
        html = self.render()

        assert 'hx-get="/members/role/member?group=RT%20Freiraum"' in html
        assert ">Mitglied</a" in html

    def test_both_halves_are_separate_links(self):
        assert self.render().count("<a ") == 2

    def test_the_pill_keeps_the_role_colouring(self):
        html = self.render(
            role="coordination", label="Koordination", extra="role-badge-past"
        )

        assert 'class="role-badge role-coordination role-badge-past"' in html
        assert "--group-hue: 200" in html

    def test_no_stray_whitespace_between_the_halves(self):
        # Flex items separated by a text node would render an extra gap.
        assert "</a\n><span" in self.render()


class TestGraphDefaults:
    def test_members_are_hidden_until_the_box_is_ticked(self):
        class FakeRequest:
            query_params: dict = {}

        # Before the form was ever submitted the defaults apply.
        assert (
            groups_controller._checkbox(FakeRequest(), "with_members", False) is False
        )

        class Submitted:
            query_params = {"submitted": "1", "with_members": "true"}

        assert groups_controller._checkbox(Submitted(), "with_members", False) is True
