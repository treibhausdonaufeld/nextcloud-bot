from datetime import datetime, timedelta

import pytest
import pytz
from pydantic import ValidationError

from app.services import calendar_notifier


class DummyChannel:
    def __init__(self):
        self.sent = []

    def send_message(self, text):
        self.sent.append(text)


class DummyComponent:
    def __init__(
        self,
        start=None,
        end=None,
        summary="Test",
        uid="uid",
        description=None,
        location=None,
    ):
        self.start = start
        self.end = end
        self._summary = summary
        self._uid = uid
        self._description = description
        self._location = location

    def get(self, key):
        if key == "summary":
            return self._summary
        if key == "uid":
            return self._uid
        if key == "description":
            return self._description
        if key == "location":
            return self._location
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

    # call should not raise and should include the agenda deadline 5 days before
    route = calendar_notifier.Route(
        "wichtigstes", [timedelta(days=7)], agenda_deadline=timedelta(days=5)
    )
    notifier.send_event_notification("wichtigstes", event_data, route)

    assert sent, "Expected a message to be sent"
    sent_text, sent_channel = sent[0]
    assert "Agendapunkte" in sent_text
    assert sent_channel == "wichtigstes"
    # 5 days before start
    assert (
        "05. January 2025" in sent_text
        or "05. Jänner 2025" in sent_text
        or ("05. Januar 2025" in sent_text)
    )


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
        start = datetime.now(pytz.UTC) + timedelta(hours=1)
        notifier.check_event(
            notifier.fill_event(DummyComponent(start=start, summary=summary))
        )
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

    def test_only_the_title_is_matched(self, monkeypatch):
        from app.models.group import Group

        Group._cached_groups = [Group(name="AG Struktur", page_id=1, short_names=[])]
        sent = []
        monkeypatch.setattr(
            calendar_notifier,
            "send_message",
            lambda text, channel: sent.append(channel),
        )
        notifier = self.notifier(channel_keywords={"wichtigstes": ["plenum"]})
        start = datetime.now(pytz.UTC) + timedelta(hours=1)

        notifier.check_event(
            notifier.fill_event(
                DummyComponent(
                    start=start,
                    summary="Zahnarzt",
                    description="Danach zum Plenum, Notizen für AG Struktur",
                    location="Plenum-Raum",
                )
            )
        )

        assert sent == []

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

    route = calendar_notifier.Route(
        "wichtigstes", [timedelta(days=7)], agenda_deadline=timedelta(days=5)
    )
    # Should not raise even though dtstart key is missing
    notifier.send_event_notification("wichtigstes", event_data, route)

    assert sent, "Expected a message to be sent even without dtstart"
    sent_text, sent_channel = sent[0]
    assert "Agendapunkte" in sent_text
    assert sent_channel == "wichtigstes"
    assert "2025" in sent_text


def test_no_agenda_note_without_deadline(monkeypatch):
    notifier = calendar_notifier.Notifier(None)
    start = datetime(2025, 2, 20, 9, 30)
    event_data = notifier.fill_event(DummyComponent(start=start))

    sent = []
    monkeypatch.setattr(
        calendar_notifier, "send_message", lambda text, channel: sent.append(text)
    )
    notifier.send_event_notification("wichtigstes", event_data)

    assert sent and "Agendapunkte" not in sent[0]


class TestConfigFormat:
    """The YAML shapes `channel_keywords` and the lead times accept."""

    def config(self, **data):
        from app.services.config import CalendarNotifierConfig

        return CalendarNotifierConfig.model_validate(data)

    def test_short_form_still_works(self):
        config = self.config(channel_keywords={"allgemein": ["Plenum", "vv"]})

        entry = config.channel_keywords["allgemein"]
        assert entry.keywords == ["plenum", "vv"]
        assert entry.notify_before is None
        assert entry.channel is None

    def test_long_form(self):
        config = self.config(
            channel_keywords={
                "garten": {
                    "keywords": ["gießen"],
                    "notify_before": ["2h", "3d"],
                    "agenda_deadline": "5d",
                    "channel": "garten-chat",
                }
            }
        )

        entry = config.channel_keywords["garten"]
        assert entry.notify_before == [timedelta(days=3), timedelta(hours=2)]
        assert entry.agenda_deadline == timedelta(days=5)
        assert entry.channel == "garten-chat"

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("7d", timedelta(days=7)),
            ("36h", timedelta(hours=36)),
            ("2d12h", timedelta(days=2, hours=12)),
            ("1w", timedelta(weeks=1)),
            ("90m", timedelta(minutes=90)),
            ("2d 6h", timedelta(days=2, hours=6)),
            (3, timedelta(days=3)),
            ("3", timedelta(days=3)),
            (0.5, timedelta(hours=12)),
        ],
    )
    def test_durations(self, value, expected):
        assert self.config(notify_before=value).notify_before == [expected]

    @pytest.mark.parametrize("value", ["", "soon", "7x", "d", 0, "0h", -1])
    def test_invalid_durations_are_rejected(self, value):
        with pytest.raises(ValidationError):
            self.config(notify_before=value)

    def test_default_falls_back_to_search_end_days(self):
        assert self.config(search_end_days=5).default_notify_before() == [
            timedelta(days=5)
        ]
        assert self.config(notify_before="2d").default_notify_before() == [
            timedelta(days=2)
        ]

    def test_max_lead_time_covers_every_entry(self):
        config = self.config(
            notify_before="2d",
            group_channel_notify_before="4d",
            channel_keywords={"a": {"keywords": ["x"], "notify_before": ["10d"]}},
        )

        assert config.max_lead_time() == timedelta(days=10)


class TestReminderTiming:
    """`check_event` sends each lead time's reminder once, when it is due."""

    NOW = datetime(2026, 5, 1, 12, 0, tzinfo=pytz.UTC)

    @pytest.fixture
    def sent(self, monkeypatch):
        sent = []
        monkeypatch.setattr(
            calendar_notifier,
            "send_message",
            lambda text, channel: sent.append((channel, text)),
        )
        return sent

    def notifier(self, **data):
        from app.services.config import CalendarNotifierConfig

        data.setdefault("group_channel_fallback", False)
        data.setdefault(
            "channel_keywords",
            {
                "allgemein": ["plenum"],
                "garten": {"keywords": ["gießen"], "notify_before": ["3d", "2h"]},
            },
        )
        return calendar_notifier.Notifier(CalendarNotifierConfig.model_validate(data))

    def check(self, notifier, summary, start, now=None, uid="uid-1"):
        event = notifier.fill_event(
            DummyComponent(start=start, summary=summary, uid=uid)
        )
        notifier.check_event(event, now=now or self.NOW)

    def test_not_due_yet(self, sent):
        notifier = self.notifier(notify_before="7d")

        self.check(notifier, "Plenum", self.NOW + timedelta(days=8))

        assert sent == []

    def test_due_is_sent_once(self, sent):
        notifier = self.notifier(notify_before="7d")
        start = self.NOW + timedelta(days=6)

        self.check(notifier, "Plenum", start)
        self.check(notifier, "Plenum", start, now=self.NOW + timedelta(hours=1))

        assert [channel for channel, _ in sent] == ["allgemein"]

    def test_channel_lead_times_override_the_default(self, sent):
        notifier = self.notifier(notify_before="7d")
        start = self.NOW + timedelta(days=5)

        self.check(notifier, "Gießen", start)
        assert sent == []

        self.check(notifier, "Gießen", start, now=start - timedelta(days=3))
        assert len(sent) == 1

        self.check(notifier, "Gießen", start, now=start - timedelta(hours=3))
        assert len(sent) == 1

        self.check(notifier, "Gießen", start, now=start - timedelta(hours=1))
        assert len(sent) == 2

    def test_several_due_at_once_send_one_message(self, sent):
        notifier = self.notifier()
        start = self.NOW + timedelta(hours=1)

        self.check(notifier, "Gießen", start)
        self.check(notifier, "Gießen", start, now=self.NOW + timedelta(minutes=10))

        assert len(sent) == 1

    def test_started_events_are_skipped(self, sent):
        notifier = self.notifier(notify_before="7d")

        self.check(notifier, "Plenum", self.NOW - timedelta(minutes=5))

        assert sent == []

    def test_recurring_instances_are_announced_separately(self, sent):
        notifier = self.notifier(notify_before="7d")

        self.check(notifier, "Plenum", self.NOW + timedelta(days=1))
        self.check(
            notifier,
            "Plenum",
            self.NOW + timedelta(days=8),
            now=self.NOW + timedelta(days=2),
        )

        assert len(sent) == 2

    def test_legacy_uid_state_is_not_announced_again(self, sent):
        notifier = self.notifier()
        notifier.events["events"]["uid-1"] = self.NOW.timestamp()
        start = self.NOW + timedelta(days=2)

        self.check(notifier, "Gießen", start)
        assert sent == []

        # the later reminder still goes out
        self.check(notifier, "Gießen", start, now=start - timedelta(hours=1))
        assert len(sent) == 1

    def test_channel_override(self, sent):
        notifier = self.notifier(
            channel_keywords={
                "ernte": {"channel": "garten", "keywords": ["ernte"]},
            },
            notify_before="1d",
        )

        self.check(notifier, "Ernte", self.NOW + timedelta(hours=5))

        assert [channel for channel, _ in sent] == ["garten"]

    def test_group_fallback_lead_time(self, sent):
        from app.models.group import Group

        Group._cached_groups = [Group(name="AG Struktur", page_id=1, short_names=[])]
        notifier = self.notifier(
            group_channel_fallback=True,
            notify_before="7d",
            group_channel_notify_before="1d",
        )
        start = self.NOW + timedelta(days=2)

        self.check(notifier, "AG Struktur Treffen", start)
        assert sent == []

        self.check(
            notifier, "AG Struktur Treffen", start, now=start - timedelta(hours=20)
        )
        assert [channel for channel, _ in sent] == ["ag-struktur"]

    def test_all_day_event(self, sent):
        from datetime import date

        notifier = self.notifier(notify_before="1d")

        self.check(notifier, "Plenum", date(2026, 5, 2))

        assert len(sent) == 1

    def test_agenda_deadline_note(self, sent):
        notifier = self.notifier(
            channel_keywords={
                "wichtigstes": {
                    "keywords": ["kk"],
                    "notify_before": "10d",
                    "agenda_deadline": "5d",
                }
            }
        )

        self.check(notifier, "KK Treffen", self.NOW + timedelta(days=9))

        assert "Agendapunkte" in sent[0][1]
