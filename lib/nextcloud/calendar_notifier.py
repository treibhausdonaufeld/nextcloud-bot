import locale
import logging
import random
import time
from datetime import datetime, timedelta
from typing import Any

import caldav
import pytz
import requests
from pycouchdb.client import Database
from pycouchdb.exceptions import NotFound

from lib.couchdb import couchdb
from lib.nextcloud.config import CalendarNotifierConfig
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
    chat_url: str = ""

    chromadb_events_key = "calendar_notifier_events"
    events: dict[str, Any]  # document from couchdb
    events_processed: dict[str, float]

    calendar: caldav.Calendar
    couchdb: Database

    def __init__(self, config: CalendarNotifierConfig) -> None:
        self.config = cal_config = config
        self.chat_url = str(settings.rocketchat.hook_url)
        self.couchdb = couchdb()

        if cal_config is None or not cal_config.caldav_url or not cal_config.enabled:
            return

        try:
            self.events = self.couchdb.get(self.chromadb_events_key)
            self.events_processed = self.events.get("events", {})

            # cleanup events processed
            oldest = (
                time.time()
                - timedelta(days=cal_config.search_end_days + 1).total_seconds()
            )
            self.events_processed = {
                uid: timestamp
                for uid, timestamp in self.events_processed.items()
                if timestamp > oldest
            }
        except NotFound:
            self.events_processed = {}
            self.events = {
                "_id": self.chromadb_events_key,
                "events": self.events_processed,
            }

        client = caldav.DAVClient(
            url=cal_config.caldav_url,
            username=settings.nextcloud.admin_username,
            password=settings.nextcloud.admin_password,
        )
        self.calendar = client.calendar(url=cal_config.caldav_url)

    def _local_datetime(self, date) -> str:
        localtz = pytz.timezone(settings.timezone)
        locale.setlocale(locale.LC_ALL, settings.locale)
        return date.astimezone(localtz).strftime("%A, %d. %B %Y, %H:%M h")

    def notify_upcoming_events(self) -> None:
        """Send notifications to rocketchat for upcoming events to channels"""
        if not self.calendar:
            return

        events = self.calendar.date_search(
            start=datetime.now() + timedelta(days=self.config.search_start_days or 0),
            end=datetime.now() + timedelta(days=self.config.search_end_days or 7),
            expand=True,
        )

        for e in events:
            vevent = e.vobject_instance.vevent

            event_data = {
                prop: getattr(vevent, prop).value
                for prop in (
                    "summary",
                    "description",
                    "location",
                    "dtstart",
                    "dtend",
                    "uid",
                )
                if hasattr(vevent, prop)
            }

            if event_data["uid"] not in self.events_processed:
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
            f"\n - Start: **{self._local_datetime(event_data['dtstart'])}**"
            f"\n - Ende: **{self._local_datetime(event_data['dtend'])}**"
        )
        if "location" in event_data:
            text += f"\n - Ort: {event_data['location']}"
        if "description" in event_data:
            text += f"\n\n {event_data['description']}"

        if channel in ("wichtigstes", "general"):
            text += "\n---\n\n_Agendapunkte bitte bis 5 Tage vorher eintragen! "
            text += (
                f"{self._local_datetime(event_data['dtstart'] - timedelta(days=5))}_\n"
            )

        text += f"\n---\n 💪 : {random.choice(vor_ort_dabei)}"

        if "https://" in (
            event_data.get("description", "") + event_data.get("location", "")
        ):
            text += f"\n 🖥️ : {random.choice(nur_online)}"

        text += f"\n 😖 : {random.choice(kann_nicht)}"

        if self.chat_url:
            response = requests.post(
                self.chat_url,
                json={
                    "text": text,
                    "channel": channel,
                    "emoji": ":robot:",
                },
            )
            # log error if request failed
            if response.status_code != 200:
                logger.error(
                    "Failed to send notification for event %s to channel %s: %s",
                    event_data["summary"],
                    channel,
                    response.text,
                )
            else:
                logger.debug(
                    "Sent notification for event %s to channel %s",
                    event_data["summary"],
                    channel,
                )
        else:
            logger.info("Chat URL is not configured, no message sent.")

        self.events_processed[event_data["uid"]] = time.time()
