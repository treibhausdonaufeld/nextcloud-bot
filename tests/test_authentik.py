"""Tests for the authentik group membership write side."""

from unittest.mock import Mock, patch

import requests

from app.services.authentik import find_group, sync_members
from app.settings import settings


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


def configure(monkeypatch, base_url="https://auth.test", token="token"):
    monkeypatch.setattr(settings.auth, "authentik_base_url", base_url)
    monkeypatch.setattr(settings.auth, "authentik_token", token)


def group_result(users):
    return FakeResponse(
        {"results": [{"pk": "group-2", "name": "Mail", "users": list(users)}]}
    )


def users_result(uuid_to_pk):
    return FakeResponse(
        {"results": [{"pk": pk, "uuid": uuid} for uuid, pk in uuid_to_pk.items()]}
    )


def test_find_group_returns_exact_match(monkeypatch):
    configure(monkeypatch)
    response = FakeResponse(
        {
            "results": [
                {"pk": "group-1", "name": "Mail-Archiv"},
                {"pk": "group-2", "name": "Mail", "users": ["uuid-1"]},
            ]
        }
    )
    with patch("app.services.authentik.requests.get", return_value=response) as get:
        group = find_group("Mail")

    assert group == {"pk": "group-2", "name": "Mail", "users": ["uuid-1"]}
    assert get.call_args.kwargs["params"]["name"] == "Mail"


def test_sync_members_resolves_uuids_to_numeric_pks(monkeypatch):
    configure(monkeypatch)
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return FakeResponse(status_code=204)

    with (
        patch(
            "app.services.authentik.requests.get",
            side_effect=[
                group_result([1, 9]),
                users_result({"uuid-1": 1, "uuid-2": 2}),
            ],
        ),
        patch("app.services.authentik.requests.post", side_effect=fake_post),
    ):
        added, removed = sync_members("Mail", ["uuid-1", "uuid-2"])

    assert (added, removed) == (1, 1)
    assert calls == [
        ("https://auth.test/api/v3/core/groups/group-2/add_user/", {"pk": 2}),
        ("https://auth.test/api/v3/core/groups/group-2/remove_user/", {"pk": 9}),
    ]


def test_sync_members_can_keep_missing_members(monkeypatch):
    configure(monkeypatch)
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return FakeResponse(status_code=204)

    with (
        patch(
            "app.services.authentik.requests.get",
            side_effect=[
                group_result([9]),
                users_result({"uuid-2": 2}),
            ],
        ),
        patch("app.services.authentik.requests.post", side_effect=fake_post),
    ):
        added, removed = sync_members("Mail", ["uuid-2"], remove_missing=False)

    assert (added, removed) == (1, 0)
    assert calls == [
        ("https://auth.test/api/v3/core/groups/group-2/add_user/", {"pk": 2}),
    ]


def test_sync_members_skips_unknown_users(monkeypatch):
    configure(monkeypatch)
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return FakeResponse(status_code=204)

    with (
        patch(
            "app.services.authentik.requests.get",
            side_effect=[
                group_result([]),
                users_result({"uuid-1": 1}),
            ],
        ),
        patch("app.services.authentik.requests.post", side_effect=fake_post),
    ):
        added, removed = sync_members("Mail", ["uuid-1", "ghost"])

    assert (added, removed) == (1, 0)
    assert calls == [
        ("https://auth.test/api/v3/core/groups/group-2/add_user/", {"pk": 1}),
    ]


def test_sync_members_aborts_when_user_lookup_fails(monkeypatch):
    configure(monkeypatch)
    with (
        patch(
            "app.services.authentik.requests.get",
            side_effect=[
                group_result([1]),
                requests.RequestException("boom"),
            ],
        ),
        patch("app.services.authentik.requests.post") as post,
    ):
        assert sync_members("Mail", ["uuid-1"]) == (0, 0)
    post.assert_not_called()


def test_sync_members_skips_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings.auth, "authentik_base_url", None)
    monkeypatch.setattr(settings.auth, "authentik_token", "")
    with patch("app.services.authentik.requests.get") as get:
        assert sync_members("Mail", ["uuid-1"]) == (0, 0)
    get.assert_not_called()


def test_sync_members_skips_without_group_name(monkeypatch):
    configure(monkeypatch)
    with patch("app.services.authentik.requests.get") as get:
        assert sync_members("", ["uuid-1"]) == (0, 0)
    get.assert_not_called()


def test_sync_members_returns_zero_when_group_missing(monkeypatch):
    configure(monkeypatch)
    with patch(
        "app.services.authentik.requests.get",
        return_value=FakeResponse({"results": []}),
    ):
        assert sync_members("Mail", ["uuid-1"]) == (0, 0)


def test_sync_members_keeps_going_when_one_call_fails(monkeypatch):
    configure(monkeypatch)
    post = Mock(
        side_effect=[requests.RequestException("boom"), FakeResponse(status_code=204)]
    )
    with (
        patch(
            "app.services.authentik.requests.get",
            side_effect=[
                group_result([]),
                users_result({"uuid-1": 1, "uuid-2": 2}),
            ],
        ),
        patch("app.services.authentik.requests.post", post),
    ):
        assert sync_members("Mail", ["uuid-1", "uuid-2"], remove_missing=False) == (
            1,
            0,
        )
    assert post.call_count == 2
