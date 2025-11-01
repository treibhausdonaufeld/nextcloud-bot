import logging
from functools import cached_property
from typing import Dict, List, Set

import requests
from pydantic import BaseModel, Field

from lib.couchdb import couchdb
from lib.nextcloud.models.base import CouchDBModel
from lib.nextcloud.models.group import Group
from lib.settings import settings

logger = logging.getLogger(__name__)


class OCSUser(BaseModel):
    id: str = ""
    email: str = ""

    displayname: str | None = Field(None, alias="displayname")

    # login / metadata
    enabled: bool = True
    storage_location: str | None = Field(None, alias="storageLocation")
    first_login_timestamp: int | None = Field(None, alias="firstLoginTimestamp")
    last_login_timestamp: int | None = Field(None, alias="lastLoginTimestamp")
    last_login: int | None = Field(None, alias="lastLogin")
    backend: str | None = None
    subadmin: List[str] = Field(default_factory=list)

    quota: dict | None

    manager: str | None = None
    additional_mail: List[str] = Field(default_factory=list)

    phone: str | None = None
    address: str | None = None
    website: str | None = None
    twitter: str | None = None
    fediverse: str | None = None
    organisation: str | None = None
    role: str | None = None
    headline: str | None = None
    biography: str | None = None
    profile_enabled: str | None = None
    pronouns: str | None = None

    groups: List[str] = Field(default_factory=list)
    language: str | None = None
    locale: str | None = None
    notify_email: str | None = Field(None, alias="notify_email")

    backend_capabilities: dict | None = Field(None, alias="backendCapabilities")


class NCUser(CouchDBModel):
    # allow field population via aliases (JSON uses camelCase keys)
    model_config = {"populate_by_name": True, "extra": "ignore"}
    ocs: OCSUser

    # Primary identifiers
    username: str = ""

    def build_id(self) -> str:
        return f"{type(self).__name__}:{self.username}"

    def __str__(self) -> str:
        name_parts = self.ocs.displayname.split() if self.ocs.displayname else []
        return (
            f"{name_parts[0]} {name_parts[1][0]}."
            if len(name_parts) >= 2
            else self.ocs.displayname or self.username
        )

    @cached_property
    def mention(self) -> str:
        """Return a regex matching all usernames."""
        return f"mention://user/{self.username}"


class NCUserList:
    """Load list of Nextcloud users"""

    USER_LIST_URL = "/ocs/v2.php/cloud/users/details"

    users: Dict[str, NCUser]

    def __init__(self):
        self.load_users()

    def __getitem__(self, username: str) -> NCUser:
        return self.users[username]

    def load_users(self):
        db = couchdb()

        lookup = {
            "selector": {"type": NCUser.__name__},
            "limit": 1000,
        }
        response, results = db.resource.post("_find", json=lookup)
        response.raise_for_status()

        self.users = {d["username"]: NCUser(**d) for d in results.get("docs", [])}

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

        for username, user_data in response.json()["ocs"]["data"]["users"].items():
            if "id" in user_data:
                user_data["nextcloud_id"] = user_data.pop("id")
            ocs_user = OCSUser(**user_data)
            user = NCUser(username=username, ocs=ocs_user)
            user.build_id()
            user.save()
            logger.debug("Saved user %s to CouchDB", username)

    def mails_for_groups(self, group_names: List[str]) -> Set[str]:
        """
        Return mail addresses for all users in given list of groups
        Can be either member of Group or nextcloud group specified on user
        """
        user_emails: Set[str] = set()

        for name in group_names:
            group = Group.get_by_name(name)
            user_emails |= {
                self.users[username].ocs.email
                for username in group.all_members
                if username in self.users and self.users[username].ocs
            }
            user_emails |= {
                u.ocs.email for u in self.users.values() if name in u.ocs.groups
            }

        return user_emails

    def get_all_usernames(self) -> List[str]:
        """Return mail addresses for all users in given list of groups"""
        return sorted(self.users.keys())
