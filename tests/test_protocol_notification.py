"""Tests for user name resolution in protocol notifications."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from app.models.protocol import Protocol
from app.models.user import NCUser, NCUserList
from app.services.config import OrganisationConfig


@pytest.fixture
def mock_bot_config():
    config = MagicMock()
    config.organisation = OrganisationConfig()
    return config


def _user(username: str, displayname: str) -> NCUser:
    return NCUser(username=username, displayname=displayname, enabled=True)


class TestUserDisplayNames:
    def test_display_name_resolves_and_falls_back(self):
        NCUserList._cached_users = {"uid-1": _user("uid-1", "Anna Musterfrau")}
        user_list = NCUserList()
        assert user_list.display_name("uid-1") == "Anna Musterfrau"
        assert user_list.display_name("uid-unknown") == "uid-unknown"

    def test_display_name_falls_back_on_empty_displayname(self):
        NCUserList._cached_users = {"uid-1": _user("uid-1", "")}
        assert NCUserList().display_name("uid-1") == "uid-1"

    def test_display_names_keeps_order(self):
        NCUserList._cached_users = {
            "uid-1": _user("uid-1", "Anna Musterfrau"),
            "uid-2": _user("uid-2", "Bob Beispiel"),
        }
        user_list = NCUserList()
        assert user_list.display_names(["uid-2", "uid-1", "uid-x"]) == [
            "Bob Beispiel",
            "Anna Musterfrau",
            "uid-x",
        ]


class TestNotificationUsesDisplayNames:
    def test_notify_updated_sends_names_instead_of_ids(self, mock_bot_config):
        NCUserList._cached_users = {
            "uid-mod": _user("uid-mod", "Anna Musterfrau"),
            "uid-prot": _user("uid-prot", "Bob Beispiel"),
            "uid-part": _user("uid-part", "Carla Chaos"),
            "uid-last": _user("uid-last", "Doris Dritte"),
        }

        protocol = Protocol(page_id=1, date="2026-06-19")
        protocol.moderated_by = ["uid-mod"]
        protocol.protocol_by = ["uid-prot"]
        protocol.participants = ["uid-part"]

        page = Mock()
        page.title = "2026-06-19 Test Group"
        page.content = "some protocol content"
        page.last_user_id = "uid-last"
        page.url = "https://cloud.example.org/protocol"

        messages = []
        with (
            patch("app.models.protocol.bot_config", mock_bot_config),
            patch.object(Protocol, "page", property(lambda self: page)),
            patch.object(Protocol, "is_valid_protocol_title", return_value=True),
            patch(
                "app.models.protocol.send_message",
                side_effect=lambda text, channel: messages.append(text),
            ),
        ):
            protocol.notify_updated([])

        assert messages
        message = messages[0]
        for name in ("Anna Musterfrau", "Bob Beispiel", "Carla Chaos", "Doris Dritte"):
            assert name in message
        # the raw user ids must not appear anywhere in the message text
        for uid in ("uid-mod", "uid-prot", "uid-part", "uid-last"):
            assert uid not in message
