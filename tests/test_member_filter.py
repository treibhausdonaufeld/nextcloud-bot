"""Membership is decided by an authentik group (`AUTH__MEMBER_GROUP_NAME`)."""

from unittest.mock import MagicMock, patch

import pytest

from app.models.user import NCUser, NCUserList
from app.settings import settings

AUTHENTIK_URL = "https://auth.example.org"


@pytest.fixture
def users():
    return {
        "alice": NCUser(
            username="alice",
            displayname="Alice Anders",
            enabled=True,
            authentik_groups=["Mitglieder", "Vorstand"],
        ),
        "bob": NCUser(
            username="bob",
            displayname="Bob Berger",
            enabled=True,
            authentik_groups=["Interessierte"],
        ),
        "carol": NCUser(
            username="carol",
            displayname="Carol Curie",
            enabled=False,
            authentik_groups=["Mitglieder"],
        ),
    }


@pytest.fixture
def user_list(users):
    NCUserList._cached_users = users
    return NCUserList()


def configured(group: str = "Mitglieder", base_url: str | None = AUTHENTIK_URL):
    """Patch the member group and authentik connection settings."""
    return (
        patch.object(settings.auth, "member_group_name", group),
        patch.object(settings.auth, "authentik_base_url", base_url),
    )


class TestIsMember:
    def test_only_users_in_the_group_are_members(self, user_list, users):
        group, url = configured()
        with group, url:
            assert user_list.is_member(users["alice"]) is True
            assert user_list.is_member(users["bob"]) is False

    def test_empty_group_name_lets_everyone_through(self, user_list, users):
        group, url = configured(group="")
        with group, url:
            assert user_list.member_filter_enabled() is False
            assert user_list.is_member(users["bob"]) is True

    def test_without_authentik_everyone_is_a_member(self, user_list, users):
        group, url = configured(base_url=None)
        with group, url:
            assert user_list.member_filter_enabled() is False
            assert user_list.is_member(users["bob"]) is True

    def test_other_group_name_is_honoured(self, user_list, users):
        group, url = configured(group="Vorstand")
        with group, url:
            assert user_list.is_member(users["alice"]) is True
            assert user_list.is_member(users["bob"]) is False


class TestGetMemberUsers:
    def test_disabled_users_are_excluded(self, user_list):
        group, url = configured()
        with group, url:
            assert user_list.get_member_usernames() == ["alice"]

    def test_unfiltered_list_keeps_every_enabled_user(self, user_list):
        group, url = configured(group="")
        with group, url:
            assert user_list.get_member_usernames() == ["alice", "bob"]

    def test_without_synced_group_data_nobody_is_filtered_out(self, users):
        # Directly after the upgrade the authentik sync has not run yet, so
        # no user has groups — showing an empty member list would be worse
        # than showing everyone.
        for user in users.values():
            user.authentik_groups = []
        NCUserList._cached_users = users
        user_list = NCUserList()

        group, url = configured()
        with group, url:
            assert user_list.member_filter_configured() is True
            assert user_list.member_filter_enabled() is False
            assert user_list.get_member_usernames() == ["alice", "bob"]


class TestUpdateFromAuthentik:
    def _response(self, results):
        response = MagicMock()
        response.json.return_value = {"results": results, "pagination": {"next": 0}}
        response.raise_for_status.return_value = None
        return response

    def test_group_names_are_stored(self, user_list, users):
        results = [
            {
                "uuid": "alice",
                "username": "alice.anders",
                "groups_obj": [{"name": "Mitglieder"}, {"name": "Vorstand"}],
            }
        ]
        group, url = configured()
        with group, url:
            with patch.object(settings.auth, "authentik_token", "token"):
                with patch("app.models.user.requests.get") as get:
                    get.return_value = self._response(results)
                    with patch.object(NCUser, "store") as store:
                        user_list.update_from_authentik()

        assert users["alice"].authentik_groups == ["Mitglieder", "Vorstand"]
        assert users["alice"].authentik_username == "alice.anders"
        store.assert_called_once()

    def test_missing_groups_key_keeps_the_stored_groups(self, user_list, users):
        results = [{"uuid": "bob", "username": "bob.berger"}]
        group, url = configured()
        with group, url:
            with patch.object(settings.auth, "authentik_token", "token"):
                with patch("app.models.user.requests.get") as get:
                    get.return_value = self._response(results)
                    with patch.object(NCUser, "store"):
                        user_list.update_from_authentik()

        assert users["bob"].authentik_groups == ["Interessierte"]

    def test_unchanged_users_are_not_written(self, user_list):
        results = [
            {
                "uuid": "bob",
                "username": "",
                "groups_obj": [{"name": "Interessierte"}],
            }
        ]
        group, url = configured()
        with group, url:
            with patch.object(settings.auth, "authentik_token", "token"):
                with patch("app.models.user.requests.get") as get:
                    get.return_value = self._response(results)
                    with patch.object(NCUser, "store") as store:
                        user_list.update_from_authentik()

        store.assert_not_called()
