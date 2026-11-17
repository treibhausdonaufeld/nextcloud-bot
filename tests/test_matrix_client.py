"""Unit tests for the thin Matrix Client-Server API wrapper."""

import json
from unittest.mock import Mock

import pytest

from app.services.matrix import MatrixClient, MatrixError


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text or json.dumps(body or {})
        self.content = self.text.encode()

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


@pytest.fixture
def client():
    client = MatrixClient(
        homeserver_url="https://matrix.example.com/",
        access_token="token",
        server_name="example.com",
    )
    client.session = Mock()
    return client


def responses(client):
    """Requests made through the patched session as (method, url, payload)."""
    return [
        (call.args[0], call.args[1], call.kwargs.get("json"))
        for call in client.session.request.call_args_list
    ]


class TestIds:
    def test_room_alias(self, client):
        assert client.room_alias("ag-struktur") == "#ag-struktur:example.com"

    def test_user_id(self, client):
        assert client.user_id("alice") == "@alice:example.com"

    def test_full_user_id_is_kept(self, client):
        assert client.user_id("@alice:other.example") == "@alice:other.example"

    def test_user_domain_defaults_to_server_name(self, client):
        assert client.user_domain == "example.com"


class TestResolveAlias:
    def test_returns_room_id(self, client):
        client.session.request.return_value = FakeResponse(
            body={"room_id": "!abc:example.com"}
        )

        assert client.resolve_alias("#ag-struktur:example.com") == "!abc:example.com"

        method, url, _ = responses(client)[0]
        assert method == "GET"
        assert url.endswith(
            "/_matrix/client/v3/directory/room/%23ag-struktur%3Aexample.com"
        )

    def test_unknown_alias_returns_none(self, client):
        client.session.request.return_value = FakeResponse(
            status_code=404, body={"errcode": "M_NOT_FOUND", "error": "Not found"}
        )

        assert client.resolve_alias("#nope:example.com") is None

    def test_other_errors_raise(self, client):
        client.session.request.return_value = FakeResponse(
            status_code=500, body={"errcode": "M_UNKNOWN", "error": "boom"}
        )

        with pytest.raises(MatrixError):
            client.resolve_alias("#nope:example.com")


class TestCreateRoom:
    def test_creates_a_public_room(self, client):
        client.session.request.return_value = FakeResponse(
            body={"room_id": "!new:example.com"}
        )

        room_id = client.create_public_room("ag-struktur", "AG Struktur")

        assert room_id == "!new:example.com"
        _, url, payload = responses(client)[0]
        assert url.endswith("/_matrix/client/v3/createRoom")
        assert payload["room_alias_name"] == "ag-struktur"
        assert payload["name"] == "AG Struktur"
        assert payload["visibility"] == "public"
        assert payload["preset"] == "public_chat"

    def test_room_is_local_only(self, client):
        client.session.request.return_value = FakeResponse(
            body={"room_id": "!new:example.com"}
        )

        client.create_public_room("ag-struktur", "AG Struktur")

        _, _, payload = responses(client)[0]
        assert payload["creation_content"] == {"m.federate": False}

    def test_every_member_may_invite(self, client):
        client.session.request.return_value = FakeResponse(
            body={"room_id": "!new:example.com"}
        )

        client.create_public_room("ag-struktur", "AG Struktur")

        _, _, payload = responses(client)[0]
        assert payload["power_level_content_override"]["invite"] == 0

    def test_members_may_manage_but_not_change_the_address(self, client):
        client.session.request.return_value = FakeResponse(
            body={"room_id": "!new:example.com"}
        )

        client.create_public_room("ag-struktur", "AG Struktur")

        _, _, payload = responses(client)[0]
        levels = payload["power_level_content_override"]
        assert levels["users_default"] == 50
        assert levels["events"]["m.room.name"] == 0
        assert levels["events"]["m.room.topic"] == 0
        assert levels["events"]["m.room.canonical_alias"] == 100
        assert levels["events"]["m.room.power_levels"] == 100

    def test_room_is_created_without_encryption(self, client):
        client.session.request.return_value = FakeResponse(
            body={"room_id": "!new:example.com"}
        )

        client.create_public_room("ag-struktur", "AG Struktur")

        _, _, payload = responses(client)[0]
        initial_types = {state["type"] for state in payload["initial_state"]}
        assert "m.room.encryption" not in initial_types

    def test_creates_a_space(self, client):
        client.session.request.return_value = FakeResponse(
            body={"room_id": "!space:example.com"}
        )

        room_id = client.create_space("alle", "Alle")

        assert room_id == "!space:example.com"
        _, url, payload = responses(client)[0]
        assert url.endswith("/_matrix/client/v3/createRoom")
        assert payload["creation_content"] == {
            "m.federate": False,
            "type": "m.space",
        }
        assert payload["preset"] == "public_chat"
        assert payload["power_level_content_override"]["users_default"] == 50

    def test_taken_alias_falls_back_to_the_existing_room(self, client):
        client.session.request.side_effect = [
            FakeResponse(status_code=400, body={"errcode": "M_ROOM_IN_USE"}),
            FakeResponse(body={"room_id": "!existing:example.com"}),
        ]

        assert (
            client.create_public_room("ag-struktur", "AG Struktur")
            == "!existing:example.com"
        )


class TestSpaces:
    def test_child_is_attached_with_server_via(self, client):
        client.session.request.side_effect = [
            FakeResponse(status_code=404, body={"errcode": "M_NOT_FOUND"}),
            FakeResponse(body={}),
        ]

        client.add_space_child("!space:example.com", "!room:example.com", order="01")

        method, url, payload = responses(client)[-1]
        assert method == "PUT"
        assert url.endswith(
            "/_matrix/client/v3/rooms/%21space%3Aexample.com"
            "/state/m.space.child/%21room%3Aexample.com"
        )
        assert payload == {"via": ["example.com"], "order": "01"}

    def test_an_unchanged_child_link_is_not_rewritten(self, client):
        client.session.request.return_value = FakeResponse(
            body={"via": ["example.com"], "order": "01"}
        )

        client.add_space_child("!space:example.com", "!room:example.com", order="01")

        assert [method for method, _, _ in responses(client)] == ["GET"]

    def test_parent_link_is_written_to_the_child(self, client):
        client.session.request.side_effect = [
            FakeResponse(status_code=404, body={"errcode": "M_NOT_FOUND"}),
            FakeResponse(body={}),
        ]

        client.add_space_parent("!room:example.com", "!space:example.com")

        method, url, payload = responses(client)[-1]
        assert method == "PUT"
        assert url.endswith(
            "/_matrix/client/v3/rooms/%21room%3Aexample.com"
            "/state/m.space.parent/%21space%3Aexample.com"
        )
        assert payload == {"via": ["example.com"]}

    def test_room_is_space_reads_the_create_event(self, client):
        client.session.request.return_value = FakeResponse(body={"type": "m.space"})

        assert client.room_is_space("!space:example.com") is True

    def test_policy_violations_report_federation_and_encryption(self, client):
        client.session.request.side_effect = [
            FakeResponse(body={"m.federate": True}),
            FakeResponse(body={"algorithm": "m.megolm.v1.aes-sha2"}),
        ]

        problems = client.room_policy_violations("!room:example.com")

        assert len(problems) == 2
        assert any("federated" in problem for problem in problems)
        assert any("encrypted" in problem for problem in problems)


class TestRoomDirectory:
    def test_visibility_is_read_from_the_directory(self, client):
        client.session.request.return_value = FakeResponse(
            body={"visibility": "public"}
        )

        assert client.directory_visibility("!room:example.com") == "public"

        method, url, _ = responses(client)[0]
        assert method == "GET"
        assert url.endswith(
            "/_matrix/client/v3/directory/list/room/%21room%3Aexample.com"
        )

    def test_publishing_lists_the_room(self, client):
        client.session.request.return_value = FakeResponse(body={})

        client.publish_room("!room:example.com")

        method, url, payload = responses(client)[0]
        assert method == "PUT"
        assert url.endswith(
            "/_matrix/client/v3/directory/list/room/%21room%3Aexample.com"
        )
        assert payload == {"visibility": "public"}


class TestMembersAndInvites:
    def test_membership_is_read_from_the_member_events(self, client):
        client.session.request.return_value = FakeResponse(
            body={
                "chunk": [
                    {
                        "state_key": "@alice:example.com",
                        "content": {"membership": "join"},
                    },
                    {
                        "state_key": "@bob:example.com",
                        "content": {"membership": "leave"},
                    },
                    {"state_key": "@broken:example.com", "content": {}},
                ]
            }
        )

        assert client.room_members("!room:example.com") == {
            "@alice:example.com": "join",
            "@bob:example.com": "leave",
        }

    def test_other_2xx_codes_count_as_success(self, client):
        client.session.request.return_value = FakeResponse(status_code=204, body={})

        client.invite("!room:example.com", "@alice:example.com")

    def test_invite_posts_the_user_id(self, client):
        client.session.request.return_value = FakeResponse(body={})

        client.invite("!room:example.com", "@alice:example.com")

        method, url, payload = responses(client)[0]
        assert method == "POST"
        assert url.endswith("/_matrix/client/v3/rooms/%21room%3Aexample.com/invite")
        assert payload == {"user_id": "@alice:example.com"}


class TestStateEvents:
    def test_state_event_content_is_read(self, client):
        client.session.request.return_value = FakeResponse(body={"users_default": 100})

        assert client.get_state_event("!room:example.com", "m.room.power_levels") == {
            "users_default": 100
        }

        method, url, _ = responses(client)[0]
        assert method == "GET"
        assert url.endswith(
            "/_matrix/client/v3/rooms/%21room%3Aexample.com/state/m.room.power_levels"
        )

    def test_unset_state_event_returns_empty(self, client):
        client.session.request.return_value = FakeResponse(
            status_code=404, body={"errcode": "M_NOT_FOUND", "error": "Not found"}
        )

        assert client.get_state_event("!room:example.com", "m.room.power_levels") == {}

    def test_state_event_is_replaced(self, client):
        client.session.request.return_value = FakeResponse(body={})

        client.put_state_event(
            "!room:example.com", "m.room.power_levels", {"users_default": 100}
        )

        method, url, payload = responses(client)[0]
        assert method == "PUT"
        assert url.endswith(
            "/_matrix/client/v3/rooms/%21room%3Aexample.com/state/m.room.power_levels"
        )
        assert payload == {"users_default": 100}


class TestRateLimiting:
    def test_retries_when_rate_limited(self, client, monkeypatch):
        monkeypatch.setattr("app.services.matrix.time.sleep", lambda _: None)
        client.session.request.side_effect = [
            FakeResponse(
                status_code=429,
                body={"errcode": "M_LIMIT_EXCEEDED", "retry_after_ms": 10},
            ),
            FakeResponse(body={"room_id": "!abc:example.com"}),
        ]

        assert client.resolve_alias("#ag-struktur:example.com") == "!abc:example.com"
        assert len(responses(client)) == 2
