from datetime import datetime, timedelta

import pytest

from lib.nextcloud import calendar_notifier


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
