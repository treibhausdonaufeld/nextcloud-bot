import logging
from typing import Dict, List, Set

import edgy
import requests
from pydantic import BaseModel, Field, field_validator

from app.models.base import BaseDBModel
from app.settings import settings

logger = logging.getLogger(__name__)


class OCSUser(BaseModel):
    """User payload as returned by the Nextcloud OCS users API."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    id: str = ""
    email: str = ""

    displayname: str | None = Field(None, alias="displayname")

    # login / metadata
    enabled: bool = True
    last_login: int | None = Field(None, alias="lastLogin")
    backend: str | None = None

    quota: dict | None = None

    additional_mail: List[str] = Field(default_factory=list)

    groups: List[str] = Field(default_factory=list)
    language: str | None = None
    locale: str | None = None

    @field_validator("quota", mode="before")
    @classmethod
    def convert_empty_list_to_none(cls, v):
        """Handle API quirk where empty quota comes as [] instead of {} or null."""
        if isinstance(v, list) and not v:
            return None
        return v


class NCUser(BaseDBModel):
    username: str = edgy.CharField(max_length=255, unique=True)
    email: str = edgy.CharField(max_length=255, default="")
    displayname: str = edgy.CharField(max_length=255, default="")
    enabled: bool = edgy.BooleanField(default=True)
    groups: List[str] = edgy.JSONField(default=list)
    language: str | None = edgy.CharField(max_length=16, null=True)
    last_login: int | None = edgy.BigIntegerField(null=True)

    natural_key_fields = ("username",)

    class Meta:
        tablename = "users"

    def __str__(self) -> str:
        name_parts = self.displayname.split() if self.displayname else []
        return (
            f"{name_parts[0]} {name_parts[1][0]}."
            if len(name_parts) >= 2
            else self.displayname or self.username
        )

    @property
    def mention(self) -> str:
        return f"mention://user/{self.username}"

    def apply_ocs(self, ocs: OCSUser) -> None:
        self.email = ocs.email or ""
        self.displayname = ocs.displayname or ""
        self.enabled = ocs.enabled
        self.groups = ocs.groups
        self.language = ocs.language
        self.last_login = ocs.last_login


class NCUserList:
    """Load list of Nextcloud users"""

    USER_LIST_URL = "/ocs/v2.php/cloud/users/details"

    # Class-level cache shared across all instances
    _cached_users: Dict[str, NCUser] | None = None

    users: Dict[str, NCUser]

    def __init__(self):
        # Use cached users if available, otherwise load from database
        if NCUserList._cached_users is not None:
            self.users = NCUserList._cached_users
        else:
            self.load_users()

    def __getitem__(self, username: str) -> NCUser:
        return self.users[username]

    def load_users(self):
        self.users = {u.username: u for u in NCUser.fetch(limit=10000)}
        # Update the class-level cache
        NCUserList._cached_users = self.users

    def get_user_by_uid(self, uid: str) -> NCUser | None:
        """Get a user by their uid."""
        return self.users.get(uid, None)

    def update_from_nextcloud(self):
        response = requests.get(
            f"{settings.nextcloud.base_url}{self.USER_LIST_URL}",
            auth=(settings.nextcloud.admin_username, settings.nextcloud.admin_password),
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
        )

        if response.status_code != 200:
            logger.error(
                "User data could not be fetched, response was %s", response.text
            )

        # Get current users from Nextcloud
        nextcloud_users = response.json()["ocs"]["data"]["users"]
        nextcloud_usernames = set(nextcloud_users.keys())

        # Get current users from the database
        current_usernames = {u.username for u in self.get_enabled_users()}

        # Save/update users from Nextcloud
        for username, user_data in nextcloud_users.items():
            if "id" in user_data:
                user_data["nextcloud_id"] = user_data.pop("id")
            ocs_user = OCSUser(**user_data)
            user = NCUser.fetch_one(username=username) or NCUser(username=username)
            user.apply_ocs(ocs_user)
            user.store()
            logger.debug("Saved user %s", username)

        # Mark users that no longer exist in Nextcloud as disabled
        users_to_disable = current_usernames - nextcloud_usernames
        for username in users_to_disable:
            user = self.users[username]
            user.enabled = False
            user.store()
            logger.info(
                "Marked user %s as disabled (no longer exists in Nextcloud)",
                username,
            )

        # Refresh cache after updating from Nextcloud
        self.load_users()

    def mails_for_groups(self, group_names: List[str]) -> Set[str]:
        """
        Return mail addresses for all users in given list of groups
        Can be either member of Group or nextcloud group specified on user
        """
        from app.models.group import Group

        user_emails: Set[str] = set()

        for name in group_names:
            try:
                group = Group.get_by_name(name)
                user_emails |= {
                    self.users[username].email
                    for username in group.all_members
                    if username in self.users
                }
            except ValueError:
                pass

            user_emails |= {u.email for u in self.users.values() if name in u.groups}

        return user_emails

    def get_all_usernames(self) -> List[str]:
        """Return all usernames."""
        return sorted(self.users.keys())

    def get_enabled_users(self) -> List[NCUser]:
        """Return all users that are currently enabled."""
        return [u for u in self.users.values() if u.enabled]

    def get_enabled_usernames(self) -> List[str]:
        """Return usernames for users that are currently enabled."""
        return [
            u.username
            for u in sorted(self.get_enabled_users(), key=lambda u: u.displayname or "")
        ]

    def get_all_emails(self) -> Set[str]:
        """Return mail addresses for all users."""
        return {u.email.lower() for u in self.users.values() if u.email}
