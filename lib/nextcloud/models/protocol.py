import logging
import re
from datetime import datetime
from functools import cached_property, lru_cache
from typing import List

from chromadb.errors import NotFoundError

from lib.chromadb import chroma_client
from lib.nextcloud.config import bot_config
from lib.nextcloud.models.decision import Decision
from lib.nextcloud.models.group import Group
from lib.settings import user_regex

from .base import CouchDBModel
from .collective_page import CollectivePage

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_protocol_collection():
    try:
        return chroma_client.get_collection(name="protocols")
    except NotFoundError:
        return chroma_client.create_collection(name="protocols")


class Protocol(CouchDBModel):
    group_id: str | None = None
    page_id: int

    date: str
    moderated_by: List[str] = []
    protocol_by: List[str] = []
    participants: List[str] = []

    def build_id(self) -> str:
        return f"{self.__class__.__name__}:{self.page_id}"

    @cached_property
    def page(self) -> CollectivePage | None:
        try:
            return CollectivePage.get_from_page_id(self.page_id)
        except ValueError:
            return None

    @cached_property
    def group(self) -> Group | None:
        if not self.group_id:
            return None
        try:
            return Group.get(self.group_id)
        except ValueError:
            return None

    @cached_property
    def date_obj(self) -> datetime | None:
        if self.date:
            return datetime.strptime(self.date.split()[0], "%Y-%m-%d")
        return None

    @property
    def group_name(self) -> str | None:
        if self.group:
            return self.group.name
        return None

    @property
    def protocol_path(self) -> str | None:
        if not self.page or not self.page.ocs:
            return None
        return self.page.ocs.filePath

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

    def save(self) -> None:
        super().save()

        # Update ChromaDB collection
        if self.page and self.page.content:
            protocol_collection = get_protocol_collection()

            protocol_collection.upsert(
                ids=[self.build_id()],
                documents=[self.page.content],
                metadatas=[
                    {
                        "page_id": self.page.id,
                        "title": self.page.title,
                        "date": self.date,
                        "group_name": self.group_name,
                    },
                ],
            )

    def extract_decisions(self) -> None:
        """Get all decisions marked with ::: success"""
        if not self.page or not self.page.content:
            return

        if self.date_obj and self.date_obj > datetime.now():
            logger.info(
                "Skipping decision extraction for future protocol %s", self.build_id()
            )
            return

        # Simple regex to find ::: success blocks
        decision_blocks = re.findall(
            r"::: success(.*?):::", self.page.content, re.DOTALL
        )

        for block in decision_blocks:
            lines = block.strip().splitlines()
            if not lines:
                continue
            title = (
                lines[0]
                .replace("**", "")
                .replace("__", "")
                .strip("Beschluss")
                .strip(":")
                .strip()
            )
            text = "\n".join(lines[1:]).strip()

            # always fill title
            if not title:
                title = text
                text = ""

            decision = Decision(
                title=title,
                text=text,
                date=self.date,
                page_id=self.page_id,
                group_id=self.group_id or "",
                group_name=self.group.name if self.group else "",
            )
            decision.save()

    def update_from_page(self) -> None:
        page = self.page
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

        self.participants = sorted(
            set(self.participants) - set(self.moderated_by) - set(self.protocol_by)
        )

        self.extract_decisions()

        self.save()
