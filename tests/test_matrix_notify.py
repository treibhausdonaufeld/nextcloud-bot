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

    def test_channel_rooms_are_never_created(self, client):
        client.resolve_alias.return_value = None
        with enabled(client):
            send_matrix_message("hi", "ag-struktur")

        client.create_public_room.assert_not_called()

    def test_missing_room_falls_through(self, client):
        client.resolve_alias.return_value = None
        with enabled(client):
            assert send_matrix_message("hi", "ag-struktur") is False

        client.send_message.assert_not_called()

    def test_api_error_falls_through(self, client):
        client.send_message.side_effect = MatrixError("boom", status=500)
        with enabled(client):
            assert send_matrix_message("hi", "ag-struktur") is False


class TestDirectMessages:
    """`@user` channels are delivered as a Matrix DM."""

    @pytest.fixture
    def dm_client(self, client):
        client.user_id.side_effect = lambda name: f"{name}:example.com"
        client.get_account_data.return_value = {}
        client.create_dm_room.return_value = "!dm:example.com"
        client.room_members.return_value = {"@max.mueller:example.com": "join"}
        return client

    def test_dm_room_is_created_and_used(self, dm_client):
        with enabled(dm_client):
            assert send_matrix_message("Protokoll geändert", "@max.mueller") is True

        dm_client.create_dm_room.assert_called_once_with("@max.mueller:example.com")
        assert dm_client.send_message.call_args[0][0] == "!dm:example.com"

    def test_new_room_is_recorded_in_m_direct(self, dm_client):
        with enabled(dm_client):
            send_matrix_message("hi", "@max.mueller")

        dm_client.set_account_data.assert_called_once_with(
            "m.direct", {"@max.mueller:example.com": ["!dm:example.com"]}
        )

    def test_existing_dm_room_is_reused(self, dm_client):
        dm_client.get_account_data.return_value = {
            "@max.mueller:example.com": ["!known:example.com"]
        }

        with enabled(dm_client):
            send_matrix_message("hi", "@max.mueller")

        dm_client.create_dm_room.assert_not_called()
        dm_client.set_account_data.assert_not_called()
        assert dm_client.send_message.call_args[0][0] == "!known:example.com"

    def test_room_the_user_left_is_replaced(self, dm_client):
        dm_client.get_account_data.return_value = {
            "@max.mueller:example.com": ["!old:example.com"]
        }
        dm_client.room_members.return_value = {"@max.mueller:example.com": "leave"}

        with enabled(dm_client):
            send_matrix_message("hi", "@max.mueller")

        dm_client.create_dm_room.assert_called_once()
        assert dm_client.send_message.call_args[0][0] == "!dm:example.com"
        # the stale room is kept in the mapping, the new one appended
        dm_client.set_account_data.assert_called_once_with(
            "m.direct",
            {"@max.mueller:example.com": ["!old:example.com", "!dm:example.com"]},
        )

    def test_unreadable_room_is_skipped(self, dm_client):
        dm_client.get_account_data.return_value = {
            "@max.mueller:example.com": ["!gone:example.com"]
        }
        dm_client.room_members.side_effect = MatrixError("gone", status=403)

        with enabled(dm_client):
            assert send_matrix_message("hi", "@max.mueller") is True

        dm_client.create_dm_room.assert_called_once()

    def test_unknown_recipient_falls_through(self, dm_client):
        dm_client.create_dm_room.side_effect = MatrixError("no such user", status=403)

        with enabled(dm_client):
            assert send_matrix_message("hi", "@nobody") is False

        dm_client.send_message.assert_not_called()

    def test_failed_bookkeeping_still_delivers(self, dm_client):
        dm_client.set_account_data.side_effect = MatrixError("nope", status=500)

        with enabled(dm_client):
            assert send_matrix_message("hi", "@max.mueller") is True

        dm_client.send_message.assert_called_once()


class TestChannelOverwrite:
    """`NOTIFY_CHANNEL_OVERWRITE` redirects every notification."""

    def config(self, bot_config_overwrite=""):
        config = MagicMock()
        config.notifier.enabled = True
        config.notifier.channel_overwrite = bot_config_overwrite
        config.notifier.default_urls = []
        config.notifier.channels = {}
        return config

    def target(self, env_overwrite, bot_config_overwrite=""):
        with patch.object(notify.settings, "notify_channel_overwrite", env_overwrite):
            with patch.object(notify, "bot_config", self.config(bot_config_overwrite)):
                return notify.target_channel("ag-struktur")

    def test_channel_is_unchanged_without_an_override(self):
        assert self.target("") == "ag-struktur"

    def test_env_override_redirects_to_a_user(self):
        assert self.target("@max.mueller") == "@max.mueller"

    def test_env_override_redirects_to_a_channel(self):
        assert self.target("bot-test") == "bot-test"

    def test_env_override_wins_over_the_bot_config(self):
        assert self.target("@max.mueller", "some-channel") == "@max.mueller"

    def test_bot_config_override_still_works(self):
        assert self.target("", "some-channel") == "some-channel"

    def test_unavailable_bot_config_does_not_break_sending(self):
        broken = MagicMock()
        type(broken).notifier = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("no config page"))
        )
        with patch.object(notify.settings, "notify_channel_overwrite", ""):
            with patch.object(notify, "bot_config", broken):
                assert notify.target_channel("ag-struktur") == "ag-struktur"

    def test_rocketchat_honours_the_global_override(self):
        """Even a direct webhook call cannot escape the override."""
        from app.services import rocketchat

        posted = []

        def fake_post(url, json, timeout):
            posted.append(json)
            return MagicMock(status_code=200)

        with patch.object(rocketchat.settings, "notify_channel_overwrite", "@max"):
            with patch.object(
                rocketchat.settings.rocketchat, "hook_url", "https://chat.example/hook"
            ):
                with patch.object(rocketchat.requests, "post", fake_post):
                    rocketchat.send_rocketchat_message("hi", "ag-struktur")

        assert [payload["channel"] for payload in posted] == ["@max"]


class TestNotifyRouting:
    """`notify.send_message` prefers Apprise; otherwise Matrix and Rocket.Chat.

    Which of the two chat backends receive the message depends on what is
    configured and on whether Matrix could deliver it — see `dual_send`.
    """

    def route(
        self,
        apprise_urls,
        matrix_delivers,
        rocketchat_configured=False,
        dual_send=True,
        matrix_configured=None,
    ):
        """Run send_message and report the channel each backend was given."""
        calls: dict[str, list[str]] = {}
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
            calls.setdefault("matrix", []).append(channel)
            return matrix_delivers

        def rocketchat(**kwargs):
            calls.setdefault("rocketchat", []).append(kwargs["channel"])

        hook = "https://chat.example/hook" if rocketchat_configured else None
        # A configured Matrix can still fail to deliver (no room for the
        # channel), so the two are separate knobs.
        enabled = matrix_delivers if matrix_configured is None else matrix_configured

        with (
            patch.object(notify, "bot_config", config),
            patch.object(notify.settings, "notify_dual_send", dual_send),
            patch.object(notify.settings, "notify_channel_overwrite", ""),
            patch.object(notify.settings.rocketchat, "hook_url", hook),
            patch.object(notify, "matrix_enabled", lambda: enabled),
            patch.object(notify.apprise, "Apprise", return_value=apprise_obj),
            patch.object(notify, "send_matrix_message", matrix),
            patch.object(notify, "send_rocketchat_message", rocketchat),
        ):
            notify.send_message("hello", "ag-struktur")

        if apprise_obj.notify.called:
            calls["apprise"] = ["ag-struktur"]
        return calls

    def test_apprise_targets_win(self):
        calls = self.route(
            apprise_urls=["json://localhost"],
            matrix_delivers=True,
            rocketchat_configured=True,
        )

        assert "apprise" in calls
        assert "matrix" not in calls
        assert "rocketchat" not in calls

    def test_matrix_only_when_rocketchat_is_not_configured(self):
        calls = self.route(apprise_urls=[], matrix_delivers=True)

        assert calls.get("matrix") == ["ag-struktur"]
        assert "rocketchat" not in calls

    def test_rocketchat_is_the_last_resort(self):
        calls = self.route(apprise_urls=[], matrix_delivers=False)

        assert calls.get("matrix") == ["ag-struktur"]
        assert calls.get("rocketchat") == ["ag-struktur"]

    def test_both_receive_the_message_when_both_are_configured(self):
        calls = self.route(
            apprise_urls=[], matrix_delivers=True, rocketchat_configured=True
        )

        assert calls.get("matrix") == ["ag-struktur"]
        assert calls.get("rocketchat") == ["ag-struktur"]

    def test_dual_send_can_be_switched_off(self):
        calls = self.route(
            apprise_urls=[],
            matrix_delivers=True,
            rocketchat_configured=True,
            dual_send=False,
        )

        assert calls.get("matrix") == ["ag-struktur"]
        assert "rocketchat" not in calls

    def test_undeliverable_matrix_message_is_not_sent_twice(self):
        """Matrix is configured but has no room for the channel."""
        calls = self.route(
            apprise_urls=[],
            matrix_delivers=False,
            matrix_configured=True,
            rocketchat_configured=True,
        )

        assert calls.get("rocketchat") == ["ag-struktur"]
