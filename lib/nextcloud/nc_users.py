import logging
from typing import List, Set

import requests

from .config import BotConfig

logger = logging.getLogger(__name__)


class NCUserList:
    """Load list of Nextcloud users"""

    USER_LIST_URL = "/ocs/v2.php/cloud/users/details"

    def __init__(self):
        self._load_users()

    def _load_users(self):
        config = BotConfig.data["nextcloud"]

        response = requests.get(
            f"{config['host']}{self.USER_LIST_URL}",
            auth=(config["username"], config["password"]),
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
        )

        if response.status_code != 200:
            logger.error(
                "User data could not be fetched, response was %s", response.text
            )
            response.raise_for_status()

        self.user_data = [
            user_data
            for username, user_data in response.json()["ocs"]["data"]["users"].items()
            if user_data["enabled"]
        ]

    def mails_for_groups(self, group_names: List[str]) -> Set[str]:
        """Return mail addresses for all users in given list of groups"""
        user_emails = set()

        for group in group_names:
            user_emails |= {u["email"] for u in self.user_data if group in u["groups"]}

        return user_emails

    def get_all_usernames(self) -> Set[str]:
        """Return mail addresses for all users in given list of groups"""
        return {u["id"] for u in self.user_data}
