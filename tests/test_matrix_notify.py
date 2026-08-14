"""Unit tests for notification delivery into Matrix rooms."""

from unittest.mock import MagicMock, patch

import pytest

from app.services import notify
from app.services.matrix import MatrixError
from app.services.matrix_notify import (
    channel_alias_localpart,
    render_markdown,
    send_matrix_message,
)

MODULE = "app.services.matrix_notify"


@pytest.fixture
def client():
    """A MatrixClient stand-in with a resolvable room."""
    client = MagicMock()
    client.room_alias.side_effect = lambda slug: f"#{slug}:example.com"
    client.resolve_alias.return_value = "!room:example.com"
    return client


def enabled(client, room_prefix=""):
    """Patch the module so Matrix is configured and returns `client`."""
    return patch.multiple(
        MODULE,
        matrix_enabled=lambda: True,
        MatrixClient=MagicMock(from_settings=lambda: client),
        settings=MagicMock(matrix=MagicMock(room_prefix=room_prefix)),
    )


class TestChannelMapping:
    def test_channel_name_maps_to_the_room_alias(self):
        with patch(f"{MODULE}.settings.matrix.room_prefix", ""):
            assert channel_alias_localpart("ag-struktur") == "ag-struktur"

    def test_group_name_is_slugged(self):
        with patch(f"{MODULE}.settings.matrix.room_prefix", ""):
            assert channel_alias_localpart("AG Struktur") == "ag-struktur"

    def test_leading_hash_is_stripped(self):
        with patch(f"{MODULE}.settings.matrix.room_prefix", ""):
            assert channel_alias_localpart("#ug-it") == "ug-it"

    def test_room_prefix_is_applied(self):
        with patch(f"{MODULE}.settings.matrix.room_prefix", "thd-"):
            assert channel_alias_localpart("ag-struktur") == "thd-ag-struktur"


class TestRenderMarkdown:
    def test_markdown_becomes_html(self):
        assert "<strong>Termin</strong>" in render_markdown("**Termin**")

    def test_embedded_html_is_sanitized(self):
        assert "<script>" not in render_markdown("<script>alert(1)</script> hi")


class TestSendMatrixMessage:
    def test_message_is_sent_to_the_channel_room(self, client):
        with enabled(client):
            assert send_matrix_message("**Termin**", "ag-struktur") is True

        client.resolve_alias.assert_called_once_with("#ag-struktur:example.com")
        _, kwargs = client.send_message.call_args
        assert client.send_message.call_args[0][0] == "!room:example.com"
        assert kwargs["body"] == "**Termin**"
        assert "<strong>Termin</strong>" in kwargs["formatted_body"]

    def test_disabled_when_matrix_is_not_configured(self, client):
        with patch(f"{MODULE}.matrix_enabled", return_value=False):
            assert send_matrix_message("hi", "ag-struktur") is False

        client.send_message.assert_not_called()

    def test_direct_messages_are_not_handled(self, client):
        with enabled(client):
            assert send_matrix_message("hi", "@alice") is False

        client.send_message.assert_not_called()

    def test_missing_room_falls_through(self, client):
        client.resolve_alias.return_value = None
        with enabled(client):
            assert send_matrix_message("hi", "ag-struktur") is False

        client.send_message.assert_not_called()

    def test_api_error_falls_through(self, client):
        client.send_message.side_effect = MatrixError("boom", status=500)
        with enabled(client):
            assert send_matrix_message("hi", "ag-struktur") is False


class TestNotifyRouting:
    """`notify.send_message` prefers Apprise, then Matrix, then Rocket.Chat."""

    def route(self, apprise_urls, matrix_delivers):
        """Run send_message and report which backend was used."""
        calls = {}
        config = MagicMock()
        config.notifier.enabled = True
        config.notifier.channel_overwrite = ""
        config.notifier.default_urls = apprise_urls
        config.notifier.channels = {}
        config.notifier.title = "bot"

        apprise_obj = MagicMock()
        apprise_obj.add.return_value = True
        apprise_obj.notify.return_value = True

        def matrix(text, channel):
            calls["matrix"] = channel
            return matrix_delivers

        def rocketchat(**kwargs):
            calls["rocketchat"] = kwargs["channel"]

        with patch.object(notify, "bot_config", config):
            with patch.object(notify.apprise, "Apprise", return_value=apprise_obj):
                with patch.object(notify, "send_matrix_message", matrix):
                    with patch.object(notify, "send_rocketchat_message", rocketchat):
                        notify.send_message("hello", "ag-struktur")

        if apprise_obj.notify.called:
            calls["apprise"] = True
        return calls

    def test_apprise_targets_win(self):
        calls = self.route(apprise_urls=["json://localhost"], matrix_delivers=True)

        assert "apprise" in calls
        assert "matrix" not in calls
        assert "rocketchat" not in calls

    def test_matrix_is_used_when_no_apprise_target_exists(self):
        calls = self.route(apprise_urls=[], matrix_delivers=True)

        assert calls.get("matrix") == "ag-struktur"
        assert "rocketchat" not in calls

    def test_rocketchat_is_the_last_resort(self):
        calls = self.route(apprise_urls=[], matrix_delivers=False)

        assert calls.get("matrix") == "ag-struktur"
        assert calls.get("rocketchat") == "ag-struktur"
