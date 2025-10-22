import json
import locale
import logging
import random
import time
from datetime import datetime, timedelta

import caldav
import pytz
import requests

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
    calendar = None
    config = None

    def __init__(self, config):
        self.config = cal_config = config.get("calendar_notifier")
        self.chat_url = config.get("notifier", {}).get("chat_url", "")
        if (
            cal_config is None
            or not cal_config.get("caldav_url")
            or not cal_config["enabled"]
        ):
            return

        try:
            self.events_processed = json.load(open(self.config["processed_storage"]))

            # cleanup events processed
            oldest = (
                time.time()
                - timedelta(days=self.config["search_end_days"] + 1).total_seconds()
            )
            self.events_processed = {
                uid: timestamp
                for uid, timestamp in self.events_processed.items()
                if timestamp > oldest
            }
        except FileNotFoundError:
            self.events_processed = {}

        client = caldav.DAVClient(
            url=cal_config["caldav_url"],
            username=config["nextcloud"]["username"],
            password=config["nextcloud"]["password"],
        )
        self.calendar = client.calendar(url=cal_config["caldav_url"])

    def _local_datetime(self, date) -> str:
        localtz = pytz.timezone(settings.timezone)
        locale.setlocale(locale.LC_ALL, settings.locale)
        return date.astimezone(localtz).strftime("%A, %d. %B %Y, %H:%M h")

    def notify_upcoming_events(self) -> None:
        """Send notifications to rocketchat for upcoming events to channels"""
        if not self.calendar:
            return

        events = self.calendar.date_search(
            start=datetime.now().date()
            + timedelta(days=self.config.get("search_start_days", 0)),
            end=datetime.now() + timedelta(days=self.config.get("search_end_days", 7)),
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

        json.dump(self.events_processed, open(self.config["processed_storage"], "w"))

    def check_event(self, event_data):
        for channel, keywords in self.config["channel_keywords"].items():
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
