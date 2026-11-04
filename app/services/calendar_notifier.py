import logging
import random
import time
from datetime import date as dt_date
from datetime import datetime, timedelta
from typing import Any, NamedTuple, Optional

import caldav
import pytz

from app.models.kv import get_state, set_state
from app.services.config import CalendarNotifierConfig
from app.services.notify import send_message
from app.settings import settings

logger = logging.getLogger(__name__)

vor_ort_dabei = [
    "bin vor Ort dabei",
    "fix dabei",
    "i pack mit an",
    "jawoi, gemma scho!",
    "fix, oida!",
    "kloa, bin dabei",
    "zählts auf mi",
    "voi dabei",
    "ur dabei",
]
nur_online = [
    "kann nur online",
    "bin online dabei",
    "nur online",
    "hintam büdschiam",
    "i bleib daham",
]
kann_nicht = [
    "kann ned",
    "nix geht",
    "ned dabei",
    "goa ned",
    "ned da",
    "des geht ned",
    "ohne mi",
    "leida ned",
]


class Route(NamedTuple):
    """Where an event is announced and how long in advance."""

    channel: str
    notify_before: list[timedelta]
    agenda_deadline: Optional[timedelta] = None


class Notifier:
    config: CalendarNotifierConfig

    processed_events_key = "calendar_notifier_events"
    events: dict[str, Any]

    calendar: Any = None

    def __init__(self, config: CalendarNotifierConfig) -> None:
        self.config = cal_config = config

        if cal_config is None or not cal_config.caldav_url or not cal_config.enabled:
            return

        self.events = get_state(self.processed_events_key, {"events": {}})

        # cleanup events processed: an entry is needed until its event has
        # started, which is at most the longest lead time after it was sent
        oldest = (
            time.time()
            - (cal_config.max_lead_time() + timedelta(days=1)).total_seconds()
        )
        self.events["events"] = {
            uid: timestamp
            for uid, timestamp in self.events.get("events", {}).items()
            if timestamp > oldest
        }

        client = caldav.get_davclient(  # type: ignore[operator]
            url=cal_config.caldav_url,
            username=settings.nextcloud.admin_username,
            password=settings.nextcloud.admin_password,
        )
        self.calendar = client.calendar(url=cal_config.caldav_url)

    def _local_datetime(self, date) -> str:
        """
        Normalize various date/time representations to a localized string.

        Accepts:
        - None -> returns empty string
        - datetime.datetime (aware or naive)
        - datetime.date -> treated as midnight on that date
        - objects with a `.dt` attribute (like some ical components) -> use `.dt`

        Ensures the resulting datetime is timezone-aware in the configured
        `settings.timezone` before formatting with locale-aware names.
        """
        if date is None:
            return ""

        # Some icalendar components expose dates via a .dt attribute
        if hasattr(date, "dt"):
            try:
                date = date.dt
            except Exception:
                # fallback: stringify
                return str(date)

        # If it's a date (no time), convert to datetime at midnight
        if isinstance(date, dt_date) and not isinstance(date, datetime):
            date = datetime.combine(date, datetime.min.time())

        # At this point we expect a datetime
        if not isinstance(date, datetime):
            # Last resort: stringify
            return str(date)

        localtz = pytz.timezone(settings.timezone)
        # If naive, assume it's in UTC then convert (safer than assuming local)
        if date.tzinfo is None:
            try:
                # First try to treat naive datetimes as UTC
                date = pytz.UTC.localize(date)
            except Exception:
                # If that fails, just attach localtz
                date = localtz.localize(date)

        # The process-wide locale is configured once at startup (setup_locale),
        # so month/day names come out in the configured language.
        return date.astimezone(localtz).strftime("%A, %d. %B %Y, %H:%M h")

    def fill_event(self, component) -> dict[str, str]:
        ## quite some data is tossed away here - like, the recurring rule.
        cur = {}
        cur["summary"] = component.get("summary")
        cur["uid"] = str(component.get("uid") or "")
        cur["description"] = str(component.get("description"))
        cur["location"] = str(component.get("location"))
        ## month/day/year time? Never ever do that!
        ## It's one of the most confusing date formats ever!
        ## Use year-month-day time instead ... https://xkcd.com/1179/
        cur["start"] = component.start
        endDate = component.end
        if endDate:
            cur["end"] = endDate
        ## For me the following line breaks because some imported calendar events
        ## came without dtstamp.  But dtstamp is mandatory according to the RFC
        cur["datestamp"] = component.get("dtstamp").dt.strftime("%m/%d/%Y %H:%M")
        return cur

    def notify_upcoming_events(self) -> None:
        """Send reminders for upcoming events to their channels."""
        if not self.calendar:
            return

        now = datetime.now(pytz.UTC)
        events: list[Any] = self.calendar.search(
            start=now,
            end=now + self.config.max_lead_time(),
            expand=True,
            event=True,
        )

        for e in events:
            for component in e.icalendar_instance.walk():
                if component.name != "VEVENT":
                    continue

                self.check_event(self.fill_event(component), now=now)

        set_state(self.processed_events_key, self.events)

    def route(self, summary: str) -> Optional[Route]:
        """The channel (and its lead times) an event with this title goes to.

        Only the title (SUMMARY) is matched, against the configured keywords
        and the group names alike; description, location and other fields
        never route an event, so a keyword mentioned in passing in the
        description does not pull it into an unrelated channel.
        """
        if not summary:
            return None

        lowered = summary.lower()
        for name, entry in self.config.channel_keywords.items():
            if any(keyword in lowered for keyword in entry.keywords):
                return Route(
                    channel=entry.channel or name,
                    notify_before=(
                        entry.notify_before or self.config.default_notify_before()
                    ),
                    agenda_deadline=entry.agenda_deadline,
                )

        channel = self.group_channel(summary)
        if channel:
            return Route(
                channel=channel,
                notify_before=(
                    self.config.group_channel_notify_before
                    or self.config.default_notify_before()
                ),
            )
        return None

    def _aware(self, value: Any) -> Optional[datetime]:
        """Event start as an aware datetime; all-day and floating times are
        read in the calendar's timezone."""
        if value is None:
            return None
        if hasattr(value, "dt"):
            value = value.dt
        if isinstance(value, dt_date) and not isinstance(value, datetime):
            value = datetime.combine(value, datetime.min.time())
        if not isinstance(value, datetime):
            return None
        if value.tzinfo is None:
            value = pytz.timezone(self.config.timezone).localize(value)
        return value

    def check_event(self, event_data, now: Optional[datetime] = None) -> None:
        """Send the reminder for this event instance if one is due.

        Every lead time of the event's channel is one reminder, remembered
        under `uid|start|lead` so each occurrence of a recurring event (they
        share the uid) and each lead time is sent exactly once. When several
        reminders are due at once (the event was created at short notice),
        only one message goes out and the others count as sent.
        """
        route = self.route(event_data["summary"])
        if route is None:
            return

        now = now or datetime.now(pytz.UTC)
        start = self._aware(event_data.get("start"))
        if start is None or start <= now:
            return

        uid = event_data["uid"]
        processed = self.events["events"]
        # Upgrade from the uid-only state: a reminder that was already due
        # when that old announcement went out counts as sent.
        announced_at = processed.get(uid)

        due: list[str] = []
        unsent = False
        for lead in route.notify_before:
            remind_at = start - lead
            key = f"{uid}|{start.isoformat()}|{int(lead.total_seconds())}"
            if remind_at > now or key in processed:
                continue
            due.append(key)
            if announced_at is None or remind_at.timestamp() > announced_at:
                unsent = True

        if unsent:
            self.send_event_notification(route.channel, event_data, route)

        for key in due:
            processed[key] = time.time()

    def group_channel(self, summary: str) -> str:
        """Channel of the group named in the event, e.g. "ag-struktur".

        The fallback for events the configured `channel_keywords` do not
        cover: an event called "AG Struktur Treffen" is announced in the
        group's own chat channel, which the Matrix sync has already created.
        """
        if not self.config.group_channel_fallback:
            return ""

        from app.models.group import Group
        from app.services.matrix_rooms import channel_slug

        try:
            group = Group.find_in_text(summary)
        except Exception:  # group table/bot config unavailable
            logger.exception("Could not look up the group for event %r", summary)
            return ""

        if group is None:
            logger.debug("No group found for event %r", summary)
            return ""

        return channel_slug(group.name)

    def send_event_notification(
        self, channel: str, event_data, route: Optional[Route] = None
    ) -> None:
        text = (
            f"Nächster Termin: **{event_data['summary']}**"
            f"\n - Start: **{self._local_datetime(event_data['start'])}**"
            # `fill_event` omits "end" for events without DTEND
            f"\n - Ende: **{self._local_datetime(event_data.get('end'))}**"
        )
        if event_data.get("location"):
            text += f"\n - Ort: {event_data['location']}"
        if event_data.get("description"):
            text += f"\n\n {event_data['description']}"

        agenda_deadline = route.agenda_deadline if route else None
        start_dt = event_data.get("start")
        if agenda_deadline and start_dt is not None:
            if hasattr(start_dt, "dt"):
                start_dt = start_dt.dt
            try:
                deadline = self._local_datetime(start_dt - agenda_deadline)
            except Exception:
                logger.exception("Could not compute the agenda deadline")
            else:
                text += f"\n---\n\n_Agendapunkte bitte bis {deadline} eintragen!_\n"

        text += f"\n---\n 💪 : {random.choice(vor_ort_dabei)}"

        if "https://" in (
            (event_data.get("description", "") or "")
            + (event_data.get("location", "") + "")
        ):
            text += f"\n 🖥️ : {random.choice(nur_online)}"

        text += f"\n 😖 : {random.choice(kann_nicht)}"

        send_message(text, channel)
