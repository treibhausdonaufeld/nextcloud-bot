import logging
import re
from typing import List

from lib.nextcloud.config import bot_config
from lib.nextcloud.models.group import Group
from lib.settings import user_regex

from .base import CouchDBModel
from .collective_page import CollectivePage

logger = logging.getLogger(__name__)


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

    @classmethod
    def valid_title(cls, title: str) -> bool:
        """Check if the given title is a valid protocol title."""
        # Simple check: title starts with a date in YYYY-MM-DD format
        return bool(re.match(r"^\d{4}-\d{2}-\d{2} .*", title))

    @classmethod
    def is_protocol_page(cls, page: "CollectivePage") -> bool:
        protocol_kws = set(bot_config.organisation.protocol_subtype_keywords)

        return (
            len(page.ocs.filePath.split("/")) > 1
            and (
                page.is_readme
                and page.ocs.filePath.split("/")[-2].lower() in protocol_kws
            )
            or (
                not page.is_readme
                and page.ocs.filePath.split("/")[-1].lower() in protocol_kws
            )
        )

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
