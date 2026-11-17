"""Tests for the read-only external group/member API."""

import json
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from app.controllers import api as api_controller
from app.models.group import Group
from app.models.user import NCUser, NCUserList


@pytest.fixture
def active_groups():
    return [
        Group(
            name="AG Struktur",
            page_id=1,
            parent_group="Koordinationskreis",
            coordination=["alice"],
            delegate=["bob"],
            members=[],
        ),
        Group(
            name="UG IT",
            page_id=2,
            parent_group="AG Struktur",
            coordination=[],
            delegate=[],
            members=["bob", "dave"],
        ),
    ]


@pytest.fixture
def retired_group():
    # Kept in the database for history, but nobody holds a current role there.
    return Group(
        name="Archivierte AG",
        page_id=3,
        members=["carol"],
        end_date=1735689600,
    )


@pytest.fixture(autouse=True)
def data(active_groups, retired_group):
    NCUserList._cached_users = {
        "alice": NCUser(
            username="alice",
            displayname="Alice Anders",
            enabled=True,
            authentik_username="alice.anders",
        ),
        "bob": NCUser(
            username="bob",
            displayname="Bob Berger",
            enabled=True,
            authentik_username="bob.berger",
        ),
        "carol": NCUser(username="carol", displayname="Carol Curie", enabled=True),
        # dave is a current member without any group
        "dave": NCUser(username="dave", displayname="Dave Dorn", enabled=True),
        "erin": NCUser(username="erin", displayname="Erin Eder", enabled=True),
    }
    with patch.object(Group, "active_cached", return_value=active_groups):
        yield


def request_with(headers: dict[str, str] | None = None) -> Mock:
    request = Mock()
    request.headers = headers or {}
    request.cookies = {}
    request.url.path = "/api/docs"
    request.base_url = "https://nc-bot.example.com/"
    return request


class TestGroupsPayload:
    def test_lists_active_groups_with_parent_and_members(self):
        payload = api_controller.groups_payload()

        assert [group["name"] for group in payload["groups"]] == [
            "AG Struktur",
            "UG IT",
        ]
        assert payload["groups"][0]["parent_group"] == "Koordinationskreis"
        assert payload["groups"][1]["parent_group"] == "AG Struktur"
        assert datetime.fromisoformat(payload["generated_at"])

    def test_each_member_carries_the_current_role_and_identity(self):
        group = api_controller.groups_payload()["groups"][0]
        by_user = {member["username"]: member for member in group["members"]}

        assert by_user["alice"]["role"] == "coordination"
        assert by_user["alice"]["displayname"] == "Alice Anders"
        assert by_user["alice"]["authentik_username"] == "alice.anders"
        assert by_user["bob"]["role"] == "delegate"

    def test_members_are_ordered_by_seniority_then_name(self):
        group = api_controller.groups_payload()["groups"][0]

        assert [member["username"] for member in group["members"]] == ["alice", "bob"]

    def test_retired_groups_are_not_exported(self):
        names = {group["name"] for group in api_controller.groups_payload()["groups"]}

        assert "Archivierte AG" not in names

    def test_a_user_named_twice_keeps_the_more_senior_role(self):
        group = Group(
            name="AG Doppelt",
            page_id=9,
            coordination=["eve"],
            members=["eve"],
        )
        with patch.object(Group, "active_cached", return_value=[group]):
            members = api_controller.groups_payload()["groups"][0]["members"]

        assert [member["role"] for member in members] == ["coordination"]


class TestMembersPayload:
    def test_groups_the_assignments_by_member(self):
        members = {
            member["username"]: member
            for member in api_controller.members_payload()["members"]
        }

        assert [group["name"] for group in members["alice"]["groups"]] == [
            "AG Struktur"
        ]
        assert [group["role"] for group in members["bob"]["groups"]] == [
            "delegate",
            "member",
        ]
        assert [group["name"] for group in members["bob"]["groups"]] == [
            "AG Struktur",
            "UG IT",
        ]

    def test_members_without_a_group_are_included(self):
        members = {
            member["username"]: member
            for member in api_controller.members_payload()["members"]
        }

        assert members["erin"]["groups"] == []

    def test_members_of_retired_groups_have_no_current_group(self):
        members = {
            member["username"]: member
            for member in api_controller.members_payload()["members"]
        }

        assert members["carol"]["groups"] == []

    def test_both_views_describe_the_same_membership(self):
        by_group = {
            (group["name"], member["username"])
            for group in api_controller.groups_payload()["groups"]
            for member in group["members"]
        }
        by_member = {
            (group["name"], member["username"])
            for member in api_controller.members_payload()["members"]
            for group in member["groups"]
        }

        assert by_group == by_member


class TestHandlers:
    def test_groups_endpoint_returns_the_payload(self):
        response = api_controller.api_groups.fn(request_with())
        payload = json.loads(response.body)

        assert response.status_code == 200
        assert payload["groups"][0]["name"] == "AG Struktur"
        assert response.headers["cache-control"] == "no-store"

    def test_members_endpoint_is_open_to_callers_with_access(self):
        response = api_controller.api_members.fn(request_with())

        assert response.status_code == 200
        assert json.loads(response.body)["members"]

    def test_openapi_document_describes_both_endpoints(self):
        spec = json.loads(api_controller.api_openapi.fn(request_with()).body)

        assert spec["openapi"] == "3.1.0"
        assert set(spec["paths"]) == {"/api/v1/groups", "/api/v1/members"}
        assert "securitySchemes" not in spec["components"]
        assert "GroupsResponse" in spec["components"]["schemas"]
        assert "MembersResponse" in spec["components"]["schemas"]

    def test_docs_page_gets_the_spec(self):
        template = api_controller.api_docs.fn(request_with())

        assert template.name == "api_docs.html"
        assert template.context["spec"]["openapi"] == "3.1.0"
        assert template.context["base_url"] == "https://nc-bot.example.com"
