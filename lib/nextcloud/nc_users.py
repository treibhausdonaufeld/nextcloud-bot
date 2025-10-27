import logging
from typing import List, Set

import requests
from pydantic import BaseModel, Field

from lib.couchdb import couchdb
from lib.nextcloud.models.base import CouchDBModel
from lib.settings import settings

logger = logging.getLogger(__name__)


class OCSUser(BaseModel):
    id: str = ""
    email: str = ""

    displayname: str | None = Field(None, alias="displayname")
    display_name: str | None = Field(None, alias="display-name")

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
    ocs: OCSUser | None = None

    # Primary identifiers
    username: str = Field("", alias="id")

    def build_id(self) -> str:
        return f"{type(self).__name__}:{self.username}"


class NCUserList:
    """Load list of Nextcloud users"""

    USER_LIST_URL = "/ocs/v2.php/cloud/users/details"

    users: List[NCUser]

    def __init__(self):
        self.load_users()

    def load_users(self):
        db = couchdb()

        lookup = {
            "selector": {"type": NCUser.__name__},
        }
        response, results = db.resource.post("_find", json=lookup)
        response.raise_for_status()

        self.users = [NCUser(**d) for d in results.get("docs", [])]

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
            response.raise_for_status()

        for username, user_data in response.json()["ocs"]["data"]["users"].items():
            if "id" in user_data:
                user_data["nextcloud_id"] = user_data.pop("id")
            ocs_user = OCSUser(**user_data)
            user = NCUser(username=username, ocs=ocs_user)
            user.build_id()
            user.save()
            logger.debug("Saved user %s to CouchDB", username)

    def mails_for_groups(self, group_names: List[str]) -> Set[str]:
        """Return mail addresses for all users in given list of groups"""
        user_emails: Set[str] = set()

        # for group in group_names:
        #     user_emails |= {
        #         u.ocs.email if u.ocs else None
        #         for u in self.users
        #         if group in u.ocs.groups
        #     }

        return user_emails

    def get_all_usernames(self) -> Set[str]:
        """Return mail addresses for all users in given list of groups"""
        return {u.username for u in self.users}
