import locale
import logging
import random
import time
from datetime import date as dt_date
from datetime import datetime, timedelta
from typing import Any

import caldav
import pytz
from pycouchdb.client import Database
from pycouchdb.exceptions import NotFound

from lib.couchdb import couchdb
from lib.nextcloud.config import CalendarNotifierConfig
from lib.outbound.rocketchat import send_message
from lib.settings import settings

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


class Notifier:
    config: CalendarNotifierConfig

    chromadb_events_key = "calendar_notifier_events"
    events: dict[str, Any]  # document from couchdb

    calendar: caldav.Calendar
    couchdb: Database

    def __init__(self, config: CalendarNotifierConfig) -> None:
        self.config = cal_config = config
        self.couchdb = couchdb()

        if cal_config is None or not cal_config.caldav_url or not cal_config.enabled:
            return

        try:
            self.events = self.couchdb.get(self.chromadb_events_key)

            # cleanup events processed
            oldest = (
                time.time()
                - timedelta(days=cal_config.search_end_days + 1).total_seconds()
            )
            self.events["events"] = {
                uid: timestamp
                for uid, timestamp in self.events["events"].items()
                if timestamp > oldest
            }
        except NotFound:
            self.events = {
                "_id": self.chromadb_events_key,
                "events": {},
            }

        client = caldav.DAVClient(
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

        # Set locale for month/day names
        try:
            locale.setlocale(locale.LC_ALL, settings.locale)
        except Exception:
            # Ignore locale errors and proceed with defaults
            pass

        return date.astimezone(localtz).strftime("%A, %d. %B %Y, %H:%M h")

    def fill_event(self, component) -> dict[str, str]:
        ## quite some data is tossed away here - like, the recurring rule.
        cur = {}
        # cur["calendar"] = f"{calendar}"
        cur["summary"] = component.get("summary")
        cur["uid"] = str(component.get("uid") or "")
        cur["description"] = component.get("description")
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
        """Send notifications to rocketchat for upcoming events to channels"""
        if not self.calendar:
            return

        events: list[Any] = self.calendar.search(
            start=datetime.now() + timedelta(days=self.config.search_start_days or 0),
            end=datetime.now() + timedelta(days=self.config.search_end_days or 7),
            expand=True,
            event=True,
        )

        for e in events:
            for component in e.icalendar_instance.walk():
                if component.name != "VEVENT":
                    continue

                event_data = self.fill_event(component)

                if event_data["uid"] not in self.events["events"]:
                    self.check_event(event_data)

        self.couchdb.save(self.events)

    def check_event(self, event_data):
        for channel, keywords in self.config.channel_keywords.items():
            summary = event_data["summary"].lower()
            if any(s in summary for s in keywords):
                self.send_event_notification(channel, event_data)
                break

    def send_event_notification(self, channel, event_data):
        text = (
            f"Nächster Termin: **{event_data['summary']}**"
            f"\n - Start: **{self._local_datetime(event_data['start'])}**"
            f"\n - Ende: **{self._local_datetime(event_data['end'])}**"
        )
        if event_data.get("location"):
            text += f"\n - Ort: {event_data['location']}"
        if event_data.get("description"):
            text += f"\n\n {event_data['description']}"

        if channel in ("wichtigstes", "general"):
            text += "\n---\n\n_Agendapunkte bitte bis 5 Tage vorher eintragen! "
            # Prefer 'start' (set in fill_event). Some calendars may not provide
            # 'dtstart' explicitly, so handle missing values safely.
            start_dt = event_data.get("start")
            try:
                if start_dt:
                    reminder_dt = start_dt - timedelta(days=5)
                    text += f"{self._local_datetime(reminder_dt)}_\n"
                else:
                    # Fallback: no start date available
                    text += f"{self._local_datetime(None)}_\n"
            except Exception:
                # If subtraction or formatting fails, fall back to an empty string
                text += f"{self._local_datetime(None)}_\n"

        text += f"\n---\n 💪 : {random.choice(vor_ort_dabei)}"

        if "https://" in (
            (event_data.get("description", "") or "")
            + (event_data.get("location", "") + "")
        ):
            text += f"\n 🖥️ : {random.choice(nur_online)}"

        text += f"\n 😖 : {random.choice(kann_nicht)}"

        send_message(text, channel)

        self.events["events"][event_data["uid"]] = time.time()
