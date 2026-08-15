from datetime import datetime, timedelta

import pytest

from app.services import calendar_notifier


class DummyChannel:
    def __init__(self):
        self.sent = []

    def send_message(self, text):
        self.sent.append(text)


class DummyComponent:
    def __init__(self, start=None, end=None, summary="Test", uid="uid"):
        self.start = start
        self.end = end
        self._summary = summary
        self._uid = uid

    def get(self, key):
        if key == "summary":
            return self._summary
        if key == "uid":
            return self._uid
        if key == "description":
            return None
        if key == "dtstamp":
            return type("T", (), {"dt": datetime.now()})
        return None


@pytest.fixture(autouse=True)
def stub_notifier_init(monkeypatch):
    """Prevent Notifier.__init__ from performing network or couchdb calls.

    We set minimal attributes used by the methods under test.
    """

    def fake_init(self, config):
        self.config = config
        self.couchdb = None
        self.events = {"events": {}}

    monkeypatch.setattr(calendar_notifier.Notifier, "__init__", fake_init)
    yield


def test_send_event_notification_with_start(monkeypatch):
    notifier = calendar_notifier.Notifier(None)

    start = datetime(2025, 1, 10, 12, 0)
    comp = DummyComponent(
        start=start, end=start + timedelta(hours=3), summary="GGT live"
    )
    event_data = notifier.fill_event(comp)

    sent = []
    monkeypatch.setattr(
        calendar_notifier,
        "send_message",
        lambda text, channel: sent.append((text, channel)),
    )

    # call should not raise and should include the reminder 5 days before
    notifier.send_event_notification("wichtigstes", event_data)

    assert sent, "Expected a message to be sent"
    sent_text, sent_channel = sent[0]
    assert "Agendapunkte" in sent_text
    assert sent_channel == "wichtigstes"
    # 5 days before start should be present as year 2025
    assert "2025" in sent_text


class TestEventChannelRouting:
    """`check_event` maps an event to a channel, falling back to the group."""

    def notifier(self, channel_keywords=None, fallback=True):
        from app.services.config import CalendarNotifierConfig

        return calendar_notifier.Notifier(
            CalendarNotifierConfig(
                channel_keywords=channel_keywords or {},
                group_channel_fallback=fallback,
            )
        )

    def route(self, monkeypatch, summary, groups=(), **kwargs):
        """Channel `check_event` picks for an event with this summary."""
        from app.models.group import Group

        Group._cached_groups = list(groups)

        sent = []
        monkeypatch.setattr(
            calendar_notifier,
            "send_message",
            lambda text, channel: sent.append(channel),
        )

        notifier = self.notifier(**kwargs)
        notifier.check_event(notifier.fill_event(DummyComponent(summary=summary)))
        return sent

    def group(self, name, short_names=(), page_id=1):
        from app.models.group import Group

        return Group(name=name, page_id=page_id, short_names=list(short_names))

    def test_configured_keyword_wins(self, monkeypatch):
        channels = self.route(
            monkeypatch,
            "AG Struktur Treffen",
            groups=[self.group("AG Struktur")],
            channel_keywords={"wichtigstes": ["treffen"]},
        )

        assert channels == ["wichtigstes"]

    def test_group_channel_is_the_fallback(self, monkeypatch):
        channels = self.route(
            monkeypatch, "AG Struktur Treffen", groups=[self.group("AG Struktur")]
        )

        assert channels == ["ag-struktur"]

    def test_short_name_is_matched_too(self, monkeypatch):
        channels = self.route(
            monkeypatch,
            "Jour fixe der struktur-ag",
            groups=[self.group("AG Struktur", short_names=["struktur-ag"])],
        )

        assert channels == ["ag-struktur"]

    def test_longest_match_wins(self, monkeypatch):
        channels = self.route(
            monkeypatch,
            "Treffen AG Struktur Bau",
            groups=[
                self.group("AG Struktur", page_id=1),
                self.group("AG Struktur Bau", page_id=2),
            ],
        )

        assert channels == ["ag-struktur-bau"]

    def test_no_group_means_no_notification(self, monkeypatch):
        channels = self.route(
            monkeypatch, "Zahnarzt", groups=[self.group("AG Struktur")]
        )

        assert channels == []

    def test_substring_does_not_match(self, monkeypatch):
        channels = self.route(
            monkeypatch,
            "Sitzung",
            groups=[self.group("UG IT", short_names=["it"])],
        )

        assert channels == []

    def test_fallback_can_be_switched_off(self, monkeypatch):
        channels = self.route(
            monkeypatch,
            "AG Struktur Treffen",
            groups=[self.group("AG Struktur")],
            fallback=False,
        )

        assert channels == []

    def test_event_without_summary_is_ignored(self, monkeypatch):
        channels = self.route(monkeypatch, "", groups=[self.group("AG Struktur")])

        assert channels == []


def test_send_event_notification_without_dtstart_key(monkeypatch):
    notifier = calendar_notifier.Notifier(None)

    start = datetime(2025, 2, 20, 9, 30)
    comp = DummyComponent(start=start, end=start + timedelta(hours=2))
    event_data = notifier.fill_event(comp)

    # ensure explicit 'dtstart' key is not present
    event_data.pop("dtstart", None)

    sent = []
    monkeypatch.setattr(
        calendar_notifier,
        "send_message",
        lambda text, channel: sent.append((text, channel)),
    )

    # Should not raise even though dtstart key is missing
    notifier.send_event_notification("wichtigstes", event_data)

    assert sent, "Expected a message to be sent even without dtstart"
    sent_text, sent_channel = sent[0]
    assert "Agendapunkte" in sent_text
    assert sent_channel == "wichtigstes"
    assert "2025" in sent_text
