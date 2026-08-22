"""Unit tests for the group detail dialog opened from the org chart."""

from unittest.mock import patch

import pytest

from app.controllers import groups as groups_controller
from app.models.group import Group
from app.models.group_role import GroupRole
from app.models.member_leave import MemberLeave
from app.models.user import NCUser, NCUserList

JAN = 1735689600  # 2025-01-01
FEB = 1738368000  # 2025-02-01
MAR = 1740787200  # 2025-03-01
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
        # Named so the group dialog can say where the marker actually stands.
        assert by_user["bob"]["leave_group"] == "AG Garten"
        assert by_user["alice"]["on_leave"] is False
        assert by_user["alice"]["leave_group"] == ""

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


class TestFormerMemberRows:
    """The bottom of every group dialog: who was in it before, and when."""

    @staticmethod
    def role(username, role="member", start=JAN, end=MAR):
        return GroupRole(
            username=username,
            group_name="AG Haus",
            page_id=1,
            role=role,
            start_date=start,
            end_date=end,
        )

    def former(self, group, roles):
        return groups_controller.former_member_rows(group, NCUserList(), roles)

    def test_finished_roles_are_listed_with_their_period(self, group):
        rows = self.former(group, [self.role("carol", "coordination")])

        assert rows == [
            {
                "username": "carol",
                "displayname": "Carol Curie",
                "role": "coordination",
                "role_label": rows[0]["role_label"],
                "hue": groups_controller.group_hue("AG Haus"),
                "start": "2025-01-01",
                "end": "2025-03-01",
            }
        ]

    def test_current_roles_are_left_out(self, group):
        roles = [self.role("carol"), self.role("alice", end=None)]

        assert [row["username"] for row in self.former(group, roles)] == ["carol"]

    def test_every_finished_period_gets_its_own_row(self, group):
        # bob was a member, left, and came back as coordinator later
        roles = [
            self.role("bob", "member", start=JAN, end=FEB),
            self.role("bob", "coordination", start=FEB, end=MAR),
        ]

        rows = self.former(group, roles)

        assert [(row["role"], row["start"], row["end"]) for row in rows] == [
            ("coordination", "2025-02-01", "2025-03-01"),
            ("member", "2025-01-01", "2025-02-01"),
        ]

    def test_the_newest_departure_comes_first(self, group):
        roles = [
            self.role("carol", start=JAN, end=FEB),
            self.role("dave", start=JAN, end=MAR),
        ]

        assert [row["username"] for row in self.former(group, roles)] == [
            "dave",
            "carol",
        ]

    def test_unknown_users_fall_back_to_the_username(self, group):
        rows = self.former(group, [self.role("nobody")])

        assert rows[0]["displayname"] == "nobody"

    def test_a_group_nobody_ever_left_has_no_rows(self, group):
        assert self.former(group, []) == []


class TestGroupLifetime:
    def test_an_active_group_reports_its_start(self):
        group = Group(name="AG Haus", page_id=1, start_date=JAN)

        assert groups_controller.group_lifetime(group, []) == {
            "active": True,
            "started": "2025-01-01",
            "ended": "",
        }

    def test_a_retired_group_reports_both_dates(self):
        group = Group(name="AG Haus", page_id=1, start_date=JAN, end_date=MAR)

        assert groups_controller.group_lifetime(group, []) == {
            "active": False,
            "started": "2025-01-01",
            "ended": "2025-03-01",
        }

    def test_the_oldest_role_dates_a_group_stored_before_the_lifecycle(self):
        # groups parsed before start_date existed have none
        group = Group(name="AG Haus", page_id=1)
        roles = [
            GroupRole(
                username="alice",
                group_name="AG Haus",
                page_id=1,
                role="member",
                start_date=MAR,
            ),
            GroupRole(
                username="carol",
                group_name="AG Haus",
                page_id=1,
                role="member",
                start_date=JAN,
                end_date=FEB,
            ),
        ]

        assert groups_controller.group_lifetime(group, roles)["started"] == "2025-01-01"

    def test_a_role_predating_the_group_row_wins(self):
        group = Group(name="AG Haus", page_id=1, start_date=FEB)
        roles = [
            GroupRole(
                username="carol",
                group_name="AG Haus",
                page_id=1,
                role="member",
                start_date=JAN,
                end_date=FEB,
            )
        ]

        assert groups_controller.group_lifetime(group, roles)["started"] == "2025-01-01"


class TestVisibleGroups:
    """The org chart draws what exists — plus the retired group linked to."""

    @staticmethod
    def _run(groups, limit_group=""):
        with patch.object(Group, "fetch", return_value=groups):
            return groups_controller.visible_groups(limit_group)

    def test_retired_groups_are_left_out(self):
        haus = Group(name="AG Haus", page_id=1)
        alt = Group(name="AG Alt", page_id=2, end_date=MAR)

        assert self._run([haus, alt]) == [haus]

    def test_a_retired_group_is_shown_when_the_chart_is_scoped_to_it(self):
        haus = Group(name="AG Haus", page_id=1)
        alt = Group(name="AG Alt", page_id=2, end_date=MAR)
        keller = Group(name="UG Keller", page_id=3, parent_group="AG Alt", end_date=MAR)

        assert self._run([haus, alt, keller], "AG Alt") == [haus, alt, keller]

    def test_scoping_to_an_active_group_keeps_the_retired_ones_out(self):
        haus = Group(name="AG Haus", page_id=1)
        alt = Group(name="AG Alt", page_id=2, end_date=MAR)

        assert self._run([haus, alt], "AG Haus") == [haus]


class TestRoleBadgeMacro:
    """The badge is one pill with two destinations."""

    @staticmethod
    def render(group="RT Freiraum", role="member", label="Mitglied", extra=""):
        from jinja2 import Environment, FileSystemLoader

        env = Environment(loader=FileSystemLoader("app/templates"))
        macro = env.get_template("partials/badges.html").module.role_badge
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
        html = self.render()

        assert "</a><span" in html
        assert "</span\n><a" in html


def render_group_dialog(**overrides):
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
    context = {
        "_": lambda text: text,
        "_n": lambda singular, plural, n: singular if n == 1 else plural,
        "group": Group(name="RT Stiegenhaus", page_id=42),
        "subgroups": [],
        "members": [],
        "former_members": [],
        "hue": 200,
        "chat_channels": [],
        "page_url": None,
        "active": True,
        "started": "",
        "ended": "",
    }
    return env.get_template("partials/group_detail.html").render(context | overrides)


class TestGroupDialogHeader:
    """The dialog links to the wiki page the group is parsed from."""

    @staticmethod
    def render(page_url=None, short_names=()):
        return render_group_dialog(
            group=Group(
                name="RT Stiegenhaus", page_id=42, short_names=list(short_names)
            ),
            page_url=page_url,
        )

    def test_links_to_the_collectives_page(self):
        html = self.render(page_url="https://cloud.test/apps/collectives/v-1/rt-42")

        assert 'href="https://cloud.test/apps/collectives/v-1/rt-42"' in html

    def test_the_link_opens_in_a_new_tab(self):
        html = self.render(page_url="https://cloud.test/x")

        assert 'target="_blank"' in html
        assert 'rel="noopener noreferrer"' in html

    def test_a_group_without_a_stored_page_shows_no_link(self):
        html = self.render(page_url=None, short_names=["rts"])

        assert "Open in Nextcloud" not in html
        # the short names still get their row
        assert "rts" in html

    def test_the_header_row_is_dropped_when_there_is_nothing_to_show(self):
        assert "group-meta" not in self.render()


class TestRetiredGroupDialog:
    """A group that no longer exists still answers who was in it, and when."""

    def test_the_heading_marks_it_as_inactive(self):
        html = render_group_dialog(
            active=False, started="2025-01-01", ended="2025-03-01"
        )

        assert "group-retired" in html
        assert "Inactive" in html

    def test_the_lifetime_is_shown(self):
        html = render_group_dialog(
            active=False, started="2025-01-01", ended="2025-03-01"
        )

        assert "Existed from 2025-01-01 to 2025-03-01" in html

    def test_an_active_group_shows_its_start_instead(self):
        html = render_group_dialog(started="2025-01-01")

        assert "Exists since 2025-01-01" in html
        assert "group-retired" not in html

    def test_the_membership_it_had_is_still_listed(self):
        html = render_group_dialog(
            active=False,
            members=[
                {
                    "username": "carol",
                    "displayname": "Carol Curie",
                    "role": "coordination",
                    "role_label": "Koordination",
                    "hue": 200,
                    "on_leave": False,
                    "leave_until": "",
                    "leave_since": "",
                    "leave_group": "",
                }
            ],
        )

        assert "Carol Curie" in html
        # named as history rather than as the current membership
        assert "Members when it was dissolved" in html

    def test_former_members_are_listed_with_their_period(self):
        html = render_group_dialog(
            former_members=[
                {
                    "username": "carol",
                    "displayname": "Carol Curie",
                    "role": "coordination",
                    "role_label": "Koordination",
                    "hue": 200,
                    "start": "2025-01-01",
                    "end": "2025-03-01",
                }
            ]
        )

        assert "Former members" in html
        assert "2025-01-01 to 2025-03-01" in html
        assert 'hx-get="/members/user/carol"' in html
        assert "role-badge-past" in html

    def test_a_group_nobody_left_says_so(self):
        assert "Nobody has left this group yet." in render_group_dialog()


class TestRoleDialogHeading:
    """ "Mitglied von RT Stiegenhaus" — the group half is a link."""

    @staticmethod
    def render(group_name):
        from jinja2 import Environment, FileSystemLoader

        # autoescape mirrors the real app; the heading interpolates a link
        # into a translated sentence, which only works with Markup.
        env = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
        return env.get_template("partials/role_detail.html").render(
            _=lambda text: text,
            role="member",
            role_label="Mitglied",
            group_name=group_name,
            hue=200,
            current_holders=[],
            past_holders=[],
        )

    def test_the_group_is_a_link_to_its_details(self):
        html = self.render("RT Stiegenhaus")

        assert 'hx-get="/groups/detail?node=RT%20Stiegenhaus"' in html
        assert ">RT Stiegenhaus</a>" in html

    def test_the_role_label_survives_the_interpolation(self):
        assert "Mitglied" in self.render("RT Stiegenhaus")

    def test_a_role_without_a_group_has_no_link(self):
        html = self.render("")

        assert "group-crumb" not in html

    def test_the_group_name_is_still_escaped(self):
        # It comes straight from the ?group= query parameter.
        html = self.render("<script>alert(1)</script>")

        assert "<script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


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


class TestGroupsPageScript:
    """The org chart fetches /groups/graph.json with the same limit_group the
    page was scoped to. A "&" in the group name must survive that round trip
    untouched, or the fetch is scoped to a name that matches no group and the
    chart renders empty."""

    @staticmethod
    def render(limit_group="", limit_user=""):
        from jinja2 import Environment, FileSystemLoader

        env = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
        context = {
            "_": lambda text: text,
            "settings": type("Settings", (), {"name": "Bot"})(),
            "current_path": "/groups",
            "available_languages": {"de": "Deutsch"},
            "lang": "de",
            "all_groups": [],
            "users": [],
            "with_members": False,
            "with_subgroups": True,
            "limit_group": limit_group,
            "limit_user": limit_user,
            "solver": "forceAtlas2Based",
            "height": 500,
        }
        return env.get_template("groups.html").render(context)

    def test_an_ampersand_in_the_group_name_survives_the_script(self):
        import json
        import re

        html = self.render(limit_group="AG Garten & Bau")

        match = re.search(r"limit_group:\s*(\"(?:[^\"\\]|\\.)*\")", html)
        assert match, html
        assert json.loads(match.group(1)) == "AG Garten & Bau"

    def test_the_value_is_never_html_escaped_inside_the_script(self):
        # "&amp;" inside the JS string literal is a corrupted value, not the
        # literal group name — the fetch would go out with the wrong scope.
        # (Elsewhere on the page, e.g. the hidden form fields, "&amp;" is the
        # correct HTML-attribute escaping — this checks the <script> only.)
        html = self.render(limit_group="AG Garten & Bau")
        script = html.split("<script src=")[-1]

        assert "&amp;" not in script
