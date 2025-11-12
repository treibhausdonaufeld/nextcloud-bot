import logging
import time
from datetime import datetime
from typing import Any, Generator

import requests
from pycouchdb.client import Database
from pycouchdb.exceptions import NotFound

from lib.couchdb import couchdb
from lib.nextcloud.config import DeckChannelMappingItem, DeckReminderConfig
from lib.outbound.rocketchat import send_message
from lib.settings import NextcloudSettings, settings

logger = logging.getLogger(__name__)

API_VERSION = "v1.0"


class DeckReminder:
    cards_processed_key: str = "deck_reminder_cards"

    config: DeckReminderConfig
    nextcloud_config: NextcloudSettings

    couchdb: Database

    def __init__(self, config: DeckReminderConfig) -> None:
        self.config = config
        self.nextcloud_config = settings.nextcloud
        self.couchdb = couchdb()

    def remind_card_due_dates(self) -> None:
        """
        Fetch all boards from Nextcloud Deck via the API and print the due dates of cards.

        Args:
            config (dict): Configuration dictionary containing Nextcloud credentials.

        Returns:
            None
        """
        events: dict[str, Any]
        try:
            events = self.couchdb.get(self.cards_processed_key)
        except NotFound:
            events = {
                "_id": self.cards_processed_key,
                "cards": {},
            }

        now_time = time.time()

        events["last_run"] = now_time
        cards_processed = events.get("cards", {})

        for card, board_dict in self.get_due_cards():
            due_date = card["duedate"]
            due_date = datetime.strptime(due_date, "%Y-%m-%dT%H:%M:%S%z")

            days_overdue = (datetime.now(due_date.tzinfo) - due_date).days

            card_id = str(card["id"])

            if (
                now_time - cards_processed.get(card_id, 0)
                < 60 * 60 * 24 * self.config.remind_after_days
            ):
                continue

            if days_overdue >= -self.config.notify_before_days:
                self.send_card_reminder(card, days_overdue, board_dict)
                cards_processed[card_id] = now_time

        # clean up cards_processed dict, remove entries older than 30 days
        for card_id, last_run in list(cards_processed.items()):
            if now_time - last_run > 60 * 60 * 24 * 30:
                del cards_processed[card_id]

        # save events_processed to db
        events["cards"] = cards_processed
        self.couchdb.save(events)

    def send_card_reminder(
        self, card, days_overdue: int, board_dict: DeckChannelMappingItem
    ):
        """
        Send a reminder message for a specific card to a specific channel.

        Args:
            card (dict): The card to send the reminder for.
            channel (str): The channel to send the reminder to.

        Returns:
            None
        """
        channel = board_dict.channel
        board_id = board_dict.board_id

        assigned_users = card.get("assignedUsers", [])
        if not assigned_users:
            assignee_names = [card["owner"]["uid"]]  # fallback to owner
        else:
            assignee_names = [
                assignee["participant"]["uid"] for assignee in assigned_users
            ]

        pronoun = "dir" if len(assignee_names) == 1 else "euch"

        card_url = f"{settings.nextcloud.base_url}/apps/deck/board/{board_id}/card/{card['id']}"

        message = (
            f"Hallo, {', '.join([f'@{assignee}' for assignee in assignee_names])}! "
        )
        message += (
            f"Die Aufgabe [{card['title']}]({card_url}) ist {pronoun} zugewiesen und "
        )

        if days_overdue < 0:
            # card is not yet overdue, but will soon be
            message += f"sollte in {abs(days_overdue)} Tagen erledigt sein."
        else:
            # card is overdue
            message += f"überfällig seit {abs(days_overdue)} Tagen!"

        if days_overdue < 0:
            # send to each assigned user
            for assignee_name in assignee_names:
                send_message("@" + assignee_name, message)
        else:
            # send to channel
            send_message(channel, message)

    def get_due_cards(
        self,
    ) -> Generator[tuple[dict[str, Any], DeckChannelMappingItem], None, None]:
        deck_mapping = self.config.deck_channel_mapping
        # iterate over deck_mapping and fetch all cards for this board
        for board_dict in deck_mapping:
            board_id = board_dict.board_id
            channel = board_dict.channel
            logger.debug(f"Fetching stacks for board {board_id} in channel {channel}")
            try:
                stacks = self.fetch_board_stacks(board_id)
                for stack_details in stacks:
                    if stack_details.get("deletedAt", 0) != 0:
                        continue

                    for card in stack_details.get("cards", []):
                        # skip done, archived or deleted cards
                        if card["done"] or card["archived"] or card["deletedAt"] != 0:
                            continue

                        if card.get("duedate"):
                            yield card, board_dict
            except Exception as e:
                logger.exception(e)
                logger.error(f"Error fetching stacks for board {board_id}: {e}")

    def get_stack_details(self, stacks, board_id):
        """
        Process stacks and cards to find due dates.

        Args:
            stacks (list): List of stacks (as dicts) from the Nextcloud Deck API.
            board_id (str): The ID of the board.
            channel (str): The channel to send reminders to.

        Returns:
            None
        """
        for stack in stacks:
            stack_id = stack["id"]
            logger.debug(f"Fetching cards for stack {stack_id} in board {board_id}")
            try:
                yield self.fetch_stack_cards(board_id, stack_id)
            except Exception as e:
                logger.error(f"Error fetching cards for stack {stack_id}: {e}")

    def fetch_board_stacks(self, board_id):
        """
        Fetch all stacks for a specific board in Nextcloud Deck via the API.

        Args:
            board_id (str): The ID of the board to fetch stacks from.

        Returns:
            list: A list of stacks (as dicts) or raises an exception on failure.
        """
        api_url = f"{settings.nextcloud.base_url}/index.php/apps/deck/api/{API_VERSION}/boards/{board_id}/stacks"
        response = requests.get(
            api_url,
            auth=(settings.nextcloud.admin_username, settings.nextcloud.admin_password),
        )
        response.raise_for_status()
        return response.json()

    def fetch_stack_cards(self, board_id: int, stack_id: int):
        """
        Fetch all cards from a specific board in Nextcloud Deck via the API.

        Args:
            board_id (str): The ID of the board to fetch cards from.

        Returns:
            list: A list of cards (as dicts) or raises an exception on failure.
        """
        api_url = f"{settings.nextcloud.base_url}/index.php/apps/deck/api/{API_VERSION}/boards/{board_id}/stacks/{stack_id}"
        response = requests.get(
            api_url,
            auth=(settings.nextcloud.admin_username, settings.nextcloud.admin_password),
        )
        response.raise_for_status()
        return response.json()

    def fetch_nextcloud_deck_boards(self):
        """
        Fetch all boards from Nextcloud Deck via the API.

        Args:
            base_url (str): The base URL of the Nextcloud instance (e.g., 'https://cloud.example.com').
            username (str): Nextcloud username.
            password (str): Nextcloud app password or user password.

        Returns:
            list: A list of boards (as dicts) or raises an exception on failure.
        """
        api_url = f"{settings.nextcloud.base_url}/index.php/apps/deck/api/{API_VERSION}/boards"
        response = requests.get(
            api_url,
            auth=(settings.nextcloud.admin_username, settings.nextcloud.admin_password),
        )
        response.raise_for_status()
        return response.json()
