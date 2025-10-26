import logging
import re
from typing import List, cast

from lib.couchdb import couchdb
from lib.nextcloud.config import bot_config
from lib.nextcloud.models import CollectivePage, CouchDBModel
from lib.settings import user_regex

logger = logging.getLogger(__name__)


class Group(CouchDBModel):
    name: str = ""
    page_id: int
    parent_group: str | None = None

    coordination: List[str] = []
    delegate: List[str] = []
    members: List[str] = []
    short_names: List[str] = []

    def build_id(self) -> str:
        return f"{self.__class__.__name__}:{self.page_id}"

    @classmethod
    def get(cls, doc_id: str) -> "Group":
        """Get a Group by its id."""
        return cast(Group, super().get(doc_id))

    @classmethod
    def get_by_name(cls, name: str) -> "Group":
        """Get a Group by its name."""
        db = couchdb()

        lookup = {"selector": {"type": cls.__name__, "name": name}}
        response, results = db.resource.post("_find", json=lookup)
        response.raise_for_status()

        docs = results.get("docs", [])
        if not docs:
            raise ValueError(f"Group with name '{name}' not found")
        return cls(**docs[0])

    @classmethod
    def valid_name(cls, name: str) -> bool:
        """Check if the given name is a valid group name."""
        upper_name = name.upper()
        return (
            any(
                upper_name.startswith(prefix)
                for prefix in bot_config.organisation.group_prefixes
            )
            or upper_name in bot_config.organisation.extra_groups
        )

    @staticmethod
    def valid_group_names(filePath: str) -> List[str]:
        """Extract valid group names from the given file path."""
        path_parts = filePath.split("/")
        return [name for name in reversed(path_parts) if Group.valid_name(name)]

    @classmethod
    def get_for_page(cls, page: CollectivePage) -> "Group":
        """Extract the group info from the given page."""
        group_names = Group.valid_group_names(page.ocs.filePath)
        if not group_names:
            raise ValueError("Cannot determine group name from page")
        return cls.get_by_name(group_names[0])

    def update_from_page(self) -> None:
        page = CollectivePage.get_from_page_id(self.page_id)
        if not page or not page.content:
            raise ValueError("Cannot update Group: page content is missing")

        group_names = self.valid_group_names(page.ocs.filePath)
        if len(group_names) > 1:
            self.parent_group = group_names[1]
        if not group_names:
            raise ValueError("Cannot determine group name from page")

        self.name = group_names[0]

        # parse content now
        lines = page.content.splitlines()
        first_word_regex = re.compile(r"\b(\w[\w-]*)\b")

        self.coordination = []
        self.delegate = []
        self.members = []
        attr = ""

        for line in lines:
            # get the first word on the line, ignoring any leading non-word chars
            m = first_word_regex.search(line)
            if not m:
                continue
            first_word = m.group(1).lower()

            if first_word in bot_config.organisation.coordination_person_keywords:
                attr = "coordination"
            elif first_word in bot_config.organisation.delegate_person_keywords:
                attr = "delegate"
            elif first_word in bot_config.organisation.member_person_keywords:
                attr = "members"

            users = re.findall(user_regex, line)
            if users and attr:
                users_list = getattr(self, attr)
                users_list.extend(users)
                setattr(self, attr, sorted(users_list))
            elif line.strip() != "" and first_word not in (
                bot_config.organisation.coordination_person_keywords
                + bot_config.organisation.delegate_person_keywords
                + bot_config.organisation.member_person_keywords
            ):
                attr = ""

        self.save()


class Protocol(CouchDBModel):
    group_id: str | None = None
    page_id: int

    date: str
    moderated_by: List[str] = []
    protocol_by: List[str] = []
    participants: List[str] = []

    def build_id(self) -> str:
        return f"{self.__class__.__name__}:{self.page_id}"

    @property
    def group_name(self) -> str | None:
        if not self.group_id:
            return None
        group = Group.get(self.group_id)
        return group.name

    @property
    def protocol_path(self) -> str | None:
        if not self.page_id:
            return None
        page = CollectivePage.get_from_page_id(self.page_id)
        if not page or not page.ocs:
            return None
        return page.ocs.filePath

    def update_from_page(self) -> None:
        page = CollectivePage.get_from_page_id(self.page_id)
        if not page or not page.content:
            raise ValueError("Cannot update Group: page content is missing")

        self.date = page.title.split(" ")[0]  # first word as date

        try:
            self.group_id = Group.get_for_page(page).id
        except ValueError:
            pass

        lines = page.content.splitlines()
        first_word_regex = re.compile(r"\b(\w[\w-]*)\b")

        self.moderated_by = []
        self.protocol_by = []
        self.participants = []
        attr = ""

        for line in lines:
            # get the first word on the line, ignoring any leading non-word chars
            m = first_word_regex.search(line)
            if not m:
                continue
            first_word = m.group(1).lower()

            if first_word in bot_config.organisation.moderation_person_keywords:
                attr = "moderated_by"
            elif first_word in bot_config.organisation.protocol_person_keywords:
                attr = "protocol_by"
            elif first_word in bot_config.organisation.participant_person_keywords:
                attr = "participants"

            users = re.findall(user_regex, line)
            if users and attr:
                users_list = getattr(self, attr)
                users_list.extend(users)
                setattr(self, attr, sorted(users_list))
            elif line.strip() != "" and first_word not in (
                bot_config.organisation.moderation_person_keywords
                + bot_config.organisation.protocol_person_keywords
                + bot_config.organisation.participant_person_keywords
            ):
                attr = ""

        self.save()


class Decision(CouchDBModel):
    title: str
    text: str

    date: str
    protocol_id: str
    group_id: str

    external_link: str = ""
