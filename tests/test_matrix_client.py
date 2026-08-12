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

    def test_taken_alias_falls_back_to_the_existing_room(self, client):
        client.session.request.side_effect = [
            FakeResponse(status_code=400, body={"errcode": "M_ROOM_IN_USE"}),
            FakeResponse(body={"room_id": "!existing:example.com"}),
        ]

        assert (
            client.create_public_room("ag-struktur", "AG Struktur")
            == "!existing:example.com"
        )


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

    def test_invite_posts_the_user_id(self, client):
        client.session.request.return_value = FakeResponse(body={})

        client.invite("!room:example.com", "@alice:example.com")

        method, url, payload = responses(client)[0]
        assert method == "POST"
        assert url.endswith("/_matrix/client/v3/rooms/%21room%3Aexample.com/invite")
        assert payload == {"user_id": "@alice:example.com"}


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
