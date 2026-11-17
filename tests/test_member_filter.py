"""Membership is decided by the authentik groups in the bot config."""

from contextlib import contextmanager
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


@contextmanager
def configured(
    groups=("Mitglieder",),
    base_url: str | None = AUTHENTIK_URL,
    legacy_group: str = "Mitglieder",
):
    """Patch the required authentik groups, the connection and the legacy env."""
    config = MagicMock()
    config.organisation.authentik_group_membership_required = list(groups)
    with (
        patch("app.models.user.bot_config", config),
        patch.object(settings.auth, "authentik_base_url", base_url),
        patch.object(settings.auth, "member_group_name", legacy_group),
    ):
        yield config


class TestIsMember:
    def test_only_users_in_the_group_are_members(self, user_list, users):
        with configured():
            assert user_list.is_member(users["alice"]) is True
            assert user_list.is_member(users["bob"]) is False

    def test_empty_group_name_lets_everyone_through(self, user_list, users):
        with configured(groups=(), legacy_group=""):
            assert user_list.member_filter_enabled() is False
            assert user_list.is_member(users["bob"]) is True

    def test_without_authentik_everyone_is_a_member(self, user_list, users):
        with configured(base_url=None):
            assert user_list.member_filter_enabled() is False
            assert user_list.is_member(users["bob"]) is True

    def test_other_group_name_is_honoured(self, user_list, users):
        with configured(groups=("Vorstand",)):
            assert user_list.is_member(users["alice"]) is True
            assert user_list.is_member(users["bob"]) is False

    def test_any_of_several_required_groups_counts(self, user_list, users):
        with configured(groups=("Mitglieder", "Kinder")):
            assert user_list.is_member(users["alice"]) is True

    def test_empty_bot_config_falls_back_to_the_env_group(self, user_list, users):
        with configured(groups=(), legacy_group="Vorstand"):
            assert user_list.required_authentik_groups() == ["Vorstand"]
            assert user_list.is_member(users["alice"]) is True


class TestGetMemberUsers:
    def test_disabled_users_are_excluded(self, user_list):
        with configured():
            assert user_list.get_member_usernames() == ["alice"]

    def test_unfiltered_list_keeps_every_enabled_user(self, user_list):
        with configured(groups=(), legacy_group=""):
            assert user_list.get_member_usernames() == ["alice", "bob"]

    def test_without_synced_group_data_nobody_is_filtered_out(self, users):
        # Directly after the upgrade the authentik sync has not run yet, so
        # no user has groups — showing an empty member list would be worse
        # than showing everyone.
        for user in users.values():
            user.authentik_groups = []
        NCUserList._cached_users = users
        user_list = NCUserList()

        with configured():
            assert user_list.member_filter_configured() is True
            assert user_list.member_filter_enabled() is False
            assert user_list.get_member_usernames() == ["alice", "bob"]


class TestFilterMembers:
    def test_disabled_and_unknown_users_are_dropped(self, user_list):
        with configured():
            assert user_list.filter_members(["alice", "bob", "carol", "nobody"]) == [
                "alice"
            ]

    def test_order_and_second_required_group_are_honoured(self, user_list, users):
        users["bob"].authentik_groups = ["Kinder"]
        with configured(groups=("Mitglieder", "Kinder")):
            assert user_list.filter_members(["bob", "alice"]) == ["bob", "alice"]


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
        with configured():
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
        with configured():
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
        with configured():
            with patch.object(settings.auth, "authentik_token", "token"):
                with patch("app.models.user.requests.get") as get:
                    get.return_value = self._response(results)
                    with patch.object(NCUser, "store") as store:
                        user_list.update_from_authentik()

        store.assert_not_called()
