"""Unit tests for the Matrix group chat room sync."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from app.models.collective_page import CollectivePage
from app.models.group import Group
from app.services.config import OrganisationConfig
from app.services.matrix import MatrixError
from app.services.matrix_rooms import (
    MatrixRoomSync,
    channel_slug,
    group_channels,
    sync_default_rooms,
    sync_group_rooms,
)

SERVER = "example.com"


class FakeClient:
    """In-memory stand-in for `MatrixClient`, recording what was called."""

    def __init__(self, rooms=None, members=None, invite_errors=()):
        # alias -> room id
        self.rooms = dict(rooms or {})
        # room id -> {user id: membership}
        self.members = {room: dict(m) for room, m in (members or {}).items()}
        self.invite_errors = set(invite_errors)

        self.server_name = SERVER
        self.user_domain = SERVER

        self.created: list[tuple[str, str]] = []
        self.invites: list[tuple[str, str]] = []
        self.joined: list[str] = []

    def room_alias(self, localpart: str) -> str:
        return f"#{localpart}:{self.server_name}"

    def user_id(self, localpart: str) -> str:
        return f"@{localpart}:{self.user_domain}"

    def resolve_alias(self, alias: str):
        return self.rooms.get(alias)

    def create_public_room(self, localpart: str, name: str, topic: str = ""):
        alias = self.room_alias(localpart)
        room_id = f"!{localpart}:{self.server_name}"
        self.rooms[alias] = room_id
        self.members.setdefault(room_id, {})
        self.created.append((localpart, name))
        return room_id

    def join_room(self, room_id_or_alias: str):
        self.joined.append(room_id_or_alias)
        return self.rooms.get(room_id_or_alias, room_id_or_alias)

    def room_members(self, room_id: str):
        return dict(self.members.get(room_id, {}))

    def invite(self, room_id: str, user_id: str) -> None:
        if user_id in self.invite_errors:
            raise MatrixError(f"cannot invite {user_id}", status=403)
        self.invites.append((room_id, user_id))
        self.members.setdefault(room_id, {})[user_id] = "invite"


@pytest.fixture
def userlist():
    """User list that maps a Nextcloud uuid to its authentik username."""
    users = MagicMock()
    users.chat_username.side_effect = lambda username: {
        "uuid-alice": "alice",
        "uuid-bob": "bob.builder",
    }.get(username, username)
    users.get_member_users.return_value = [
        Mock(username="uuid-alice"),
        Mock(username="uuid-bob"),
    ]
    return users


def make_sync(client, userlist):
    return MatrixRoomSync(client=client, userlist=userlist)


def make_group(**kwargs):
    defaults = dict(
        name="AG Struktur",
        page_id=42,
        coordination=["uuid-alice"],
        delegate=[],
        members=["uuid-bob"],
        chat_channels=[],
    )
    defaults.update(kwargs)
    return Group(**defaults)


class TestChannelNaming:
    def test_group_name_becomes_slug(self):
        assert channel_slug("AG Struktur") == "ag-struktur"

    def test_extra_channel_name_becomes_slug(self):
        assert channel_slug("Fragen an AG Struktur") == "fragen-an-ag-struktur"

    def test_umlauts_are_transliterated(self):
        assert channel_slug("AG Öffentlichkeit & Räume") == "ag-oeffentlichkeit-raeume"

    def test_punctuation_is_collapsed(self):
        assert channel_slug("  UG IT / Infra  ") == "ug-it-infra"

    def test_group_channels_include_extras_without_duplicates(self):
        group = make_group(chat_channels=["Fragen an AG Struktur", "ag struktur"])

        assert [c.slug for c in group_channels(group)] == [
            "ag-struktur",
            "fragen-an-ag-struktur",
        ]

    def test_room_prefix_is_applied(self):
        with patch("app.services.matrix_rooms.settings") as fake_settings:
            fake_settings.matrix.room_prefix = "thd-"
            assert group_channels(make_group())[0].slug == "thd-ag-struktur"


class TestMemberMapping:
    def test_uses_authentik_username_as_localpart(self, userlist):
        sync = make_sync(FakeClient(), userlist)

        assert sync.matrix_ids(["uuid-alice", "uuid-bob"]) == [
            "@alice:example.com",
            "@bob.builder:example.com",
        ]

    def test_invalid_characters_are_dropped(self, userlist):
        userlist.chat_username.side_effect = lambda username: "Ann@ Smith"
        sync = make_sync(FakeClient(), userlist)

        assert sync.matrix_ids(["uuid-x"]) == ["@annsmith:example.com"]

    def test_full_matrix_id_is_kept_as_is(self, userlist):
        userlist.chat_username.side_effect = lambda username: "@alice:other.example"
        sync = make_sync(FakeClient(), userlist)

        assert sync.matrix_ids(["uuid-x"]) == ["@alice:other.example"]

    def test_leading_at_without_domain_gets_the_default_domain(self, userlist):
        userlist.chat_username.side_effect = lambda username: "@alice"
        sync = make_sync(FakeClient(), userlist)

        assert sync.matrix_ids(["uuid-x"]) == ["@alice:example.com"]


class TestRoomSync:
    def test_creates_public_room_and_invites_members(self, userlist):
        client = FakeClient()
        make_sync(client, userlist).sync_group(make_group())

        assert client.created == [("ag-struktur", "AG Struktur")]
        assert client.invites == [
            ("!ag-struktur:example.com", "@alice:example.com"),
            ("!ag-struktur:example.com", "@bob.builder:example.com"),
        ]

    def test_existing_room_is_reused(self, userlist):
        client = FakeClient(
            rooms={"#ag-struktur:example.com": "!room:example.com"},
            members={"!room:example.com": {}},
        )
        make_sync(client, userlist).sync_group(make_group())

        assert client.created == []
        assert [user for _, user in client.invites] == [
            "@alice:example.com",
            "@bob.builder:example.com",
        ]

    def test_members_are_never_removed_or_re_invited(self, userlist):
        client = FakeClient(
            rooms={"#ag-struktur:example.com": "!room:example.com"},
            members={
                "!room:example.com": {
                    "@alice:example.com": "join",
                    # someone who is no longer named on the page stays
                    "@carol:example.com": "join",
                }
            },
        )
        make_sync(client, userlist).sync_group(make_group())

        assert [user for _, user in client.invites] == ["@bob.builder:example.com"]
        assert client.members["!room:example.com"]["@carol:example.com"] == "join"

    def test_users_who_left_are_not_invited_again(self, userlist):
        client = FakeClient(
            rooms={"#ag-struktur:example.com": "!room:example.com"},
            members={"!room:example.com": {"@alice:example.com": "leave"}},
        )
        make_sync(client, userlist).sync_group(make_group())

        assert [user for _, user in client.invites] == ["@bob.builder:example.com"]

    def test_extra_channels_get_the_same_members(self, userlist):
        client = FakeClient()
        group = make_group(chat_channels=["Fragen an AG Struktur"])

        make_sync(client, userlist).sync_group(group)

        assert [slug for slug, _ in client.created] == [
            "ag-struktur",
            "fragen-an-ag-struktur",
        ]
        assert [user for _, user in client.invites] == [
            "@alice:example.com",
            "@bob.builder:example.com",
            "@alice:example.com",
            "@bob.builder:example.com",
        ]

    def test_failing_invite_does_not_stop_the_others(self, userlist):
        client = FakeClient(invite_errors={"@alice:example.com"})
        make_sync(client, userlist).sync_group(make_group())

        assert [user for _, user in client.invites] == ["@bob.builder:example.com"]

    def test_bot_joins_a_room_it_cannot_read(self, userlist):
        client = FakeClient(
            rooms={"#ag-struktur:example.com": "!room:example.com"},
            members={"!room:example.com": {}},
        )
        forbidden = [MatrixError("forbidden", status=403)]
        original = client.room_members

        def room_members(room_id):
            if forbidden:
                raise forbidden.pop()
            return original(room_id)

        client.room_members = room_members  # type: ignore[method-assign]

        make_sync(client, userlist).sync_group(make_group())

        assert client.joined == ["#ag-struktur:example.com"]
        assert len(client.invites) == 2

    def test_failing_channel_does_not_break_the_next_one(self, userlist):
        client = FakeClient()
        group = make_group(chat_channels=["Fragen an AG Struktur"])

        def resolve_alias(alias):
            if alias == "#ag-struktur:example.com":
                raise MatrixError("boom", status=500)
            return None

        client.resolve_alias = resolve_alias  # type: ignore[method-assign]

        make_sync(client, userlist).sync_group(group)

        assert [slug for slug, _ in client.created] == ["fragen-an-ag-struktur"]


class TestDefaultRooms:
    """`MATRIX__DEFAULT_ROOMS`: rooms every member belongs to."""

    def defaults(self, rooms, prefix=""):
        """Patch the configured default rooms for one call."""
        return patch.multiple(
            "app.services.matrix_rooms.settings.matrix",
            default_rooms=rooms,
            room_prefix=prefix,
        )

    def test_rooms_are_created_and_all_members_invited(self, userlist):
        client = FakeClient()
        with self.defaults(["Allgemein", "Ankündigungen"]):
            make_sync(client, userlist).sync_defaults()

        assert client.created == [
            ("allgemein", "Allgemein"),
            ("ankuendigungen", "Ankündigungen"),
        ]
        assert [user for _, user in client.invites] == [
            "@alice:example.com",
            "@bob.builder:example.com",
            "@alice:example.com",
            "@bob.builder:example.com",
        ]

    def test_nothing_happens_without_configured_rooms(self, userlist):
        client = FakeClient()
        with self.defaults([]):
            make_sync(client, userlist).sync_defaults()

        assert client.created == []
        assert client.invites == []

    def test_existing_members_are_not_re_invited(self, userlist):
        client = FakeClient(
            rooms={"#allgemein:example.com": "!room:example.com"},
            members={"!room:example.com": {"@alice:example.com": "join"}},
        )
        with self.defaults(["Allgemein"]):
            make_sync(client, userlist).sync_defaults()

        assert client.created == []
        assert [user for _, user in client.invites] == ["@bob.builder:example.com"]

    def test_room_prefix_applies(self, userlist):
        client = FakeClient()
        with self.defaults(["Allgemein"], prefix="thd-"):
            make_sync(client, userlist).sync_defaults()

        assert [slug for slug, _ in client.created] == ["thd-allgemein"]

    def test_only_member_users_are_invited(self, userlist):
        client = FakeClient()
        userlist.get_member_users.return_value = [Mock(username="uuid-alice")]

        with self.defaults(["Allgemein"]):
            make_sync(client, userlist).sync_defaults()

        assert [user for _, user in client.invites] == ["@alice:example.com"]

    def test_entry_point_is_disabled_without_configured_rooms(self):
        with patch("app.services.matrix_rooms.matrix_enabled", return_value=True):
            with patch("app.services.matrix_rooms.MatrixRoomSync") as factory:
                with self.defaults([]):
                    sync_default_rooms()

        factory.assert_not_called()

    def test_entry_point_never_raises(self, userlist):
        sync = make_sync(FakeClient(), userlist)
        with patch.object(sync, "sync_defaults", side_effect=RuntimeError("boom")):
            sync_default_rooms(sync=sync)


class TestSyncEntryPoint:
    def test_disabled_without_matrix_settings(self):
        with patch("app.services.matrix_rooms.matrix_enabled", return_value=False):
            with patch("app.services.matrix_rooms.MatrixRoomSync") as factory:
                sync_group_rooms(make_group())

        factory.assert_not_called()

    def test_errors_never_propagate_to_the_parser(self, userlist):
        sync = make_sync(FakeClient(), userlist)
        with patch.object(sync, "sync_group", side_effect=RuntimeError("boom")):
            sync_group_rooms(make_group(), sync=sync)


class TestDefaultRoomsSetting:
    """`MATRIX__DEFAULT_ROOMS` is a comma-separated env var."""

    def rooms(self, monkeypatch, value):
        from app.settings import Settings

        monkeypatch.setenv("MATRIX__DEFAULT_ROOMS", value)
        return Settings().matrix.default_rooms

    def test_comma_separated_list(self, monkeypatch):
        assert self.rooms(monkeypatch, "Allgemein, Ankündigungen") == [
            "Allgemein",
            "Ankündigungen",
        ]

    def test_single_room(self, monkeypatch):
        assert self.rooms(monkeypatch, "Allgemein") == ["Allgemein"]

    def test_json_list_is_accepted_too(self, monkeypatch):
        assert self.rooms(monkeypatch, '["Allgemein", "Termine"]') == [
            "Allgemein",
            "Termine",
        ]

    def test_empty_value_means_no_default_rooms(self, monkeypatch):
        assert self.rooms(monkeypatch, "") == []

    def test_unset_means_no_default_rooms(self, monkeypatch):
        from app.settings import Settings

        monkeypatch.delenv("MATRIX__DEFAULT_ROOMS", raising=False)
        assert Settings().matrix.default_rooms == []


class TestChatChannelParsing:
    """`Chat-Kanäle:` on a group page adds extra channels."""

    @pytest.fixture
    def mock_bot_config(self):
        config = MagicMock()
        config.organisation = OrganisationConfig()
        return config

    def parse(self, content, mock_bot_config):
        page = Mock(spec=CollectivePage)
        page.content = content
        page.full_path = "AG Struktur"
        page.file_path = "AG Struktur/README.md"
        page.page_id = 42
        page.emoji = "🏢"

        group = Group(name="AG Struktur", page_id=42)
        with patch("app.models.group.bot_config", mock_bot_config):
            with patch.object(CollectivePage, "get_from_page_id", return_value=page):
                with patch.object(Group, "store"):
                    group.update_from_page()
        return group

    def test_single_channel(self, mock_bot_config):
        group = self.parse(
            "# AG Struktur\n\n**Chat-Kanäle:** Fragen an AG Struktur\n",
            mock_bot_config,
        )

        assert group.chat_channels == ["Fragen an AG Struktur"]

    def test_multiple_channels(self, mock_bot_config):
        group = self.parse(
            "Chat-Kanäle: Fragen an AG Struktur, Termine\n", mock_bot_config
        )

        assert group.chat_channels == ["Fragen an AG Struktur", "Termine"]

    def test_no_keyword_means_no_extra_channels(self, mock_bot_config):
        group = self.parse("# AG Struktur\n\nmention://user/alice\n", mock_bot_config)

        assert group.chat_channels == []

    def test_channels_are_replaced_on_reparse(self, mock_bot_config):
        group = self.parse("Chat-Kanäle: Termine\n", mock_bot_config)
        assert group.chat_channels == ["Termine"]

        page = Mock(spec=CollectivePage)
        page.content = "Chat-Kanäle: Termine\n"
        page.full_path = "AG Struktur"
        page.file_path = "AG Struktur/README.md"
        page.page_id = 42
        page.emoji = ""
        with patch("app.models.group.bot_config", mock_bot_config):
            with patch.object(CollectivePage, "get_from_page_id", return_value=page):
                with patch.object(Group, "store"):
                    group.update_from_page()

        assert group.chat_channels == ["Termine"]

    def test_members_are_still_parsed_after_the_keyword(self, mock_bot_config):
        group = self.parse(
            "Chat-Kanäle: Termine\n\n**Mitglieder:**\nmention://user/alice\n",
            mock_bot_config,
        )

        assert group.chat_channels == ["Termine"]
        assert group.members == ["alice"]
