import logging
import re
from datetime import date as dateType
from datetime import datetime
from functools import cached_property
from typing import List

from lib.nextcloud.config import bot_config
from lib.nextcloud.models.decision import Decision
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

    summary_posted: bool = False

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
    def date_obj(self) -> dateType | None:
        if self.date:
            return datetime.strptime(self.date.split()[0], "%Y-%m-%d").date()
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

    def extract_decisions(self) -> None:
        """Get all decisions marked with ::: success"""
        if not self.page or not self.page.content:
            return

        if self.date_obj and self.date_obj > datetime.now().date():
            logger.info(
                "Skipping decision extraction for future protocol %s", self.build_id()
            )
            return

        # delete existing decision for this page
        for decision in Decision.get_all(selector={"page_id": self.page_id}):
            decision.delete()

        # Simple regex to find ::: success blocks
        decision_blocks = re.findall(
            r"::: success(.*?):::", self.page.content, re.DOTALL
        )

        for block in decision_blocks:
            self.save_decision(block)

    def save_decision(self, block: str) -> None:
        """Parse and save on decision from a markdown block."""

        def clean_line(line: str) -> str:
            return line.replace("**", "").replace("__", "").strip()

        lines = block.strip().splitlines()
        if not lines:
            return

        title = clean_line(lines[0])
        for title_kw in bot_config.organisation.decision_title_keywords:
            title = (
                re.sub(rf"^{title_kw}[:\s\-]*", "", title, flags=re.IGNORECASE)
                .strip(":")
                .strip()
            )
        lines[0] = ""  # remove title line

        decision = Decision(
            title=title,
            date=self.date,
            page_id=self.page_id,
            group_id=self.group_id or "",
            group_name=self.group.name if self.group else "",
        )

        # iterate over all lines and check each line for keywords
        for i, line in enumerate(lines[1:], start=1):
            line = clean_line(line)

            for valid_until_kw in bot_config.organisation.decision_valid_until_keywords:
                if re.match(rf"^{valid_until_kw}[:\s\-]*", line, flags=re.IGNORECASE):
                    decision.valid_until = re.sub(
                        rf"^{valid_until_kw}[:\s\-]*", "", line, flags=re.IGNORECASE
                    ).strip()
                    lines[i] = line = ""  # remove line

            for objection_kw in bot_config.organisation.decision_objection_keywords:
                if re.match(rf"^{objection_kw}[:\s\-]*", line, flags=re.IGNORECASE):
                    decision.objections = re.sub(
                        rf"^{objection_kw}[:\s\-]*", "", line, flags=re.IGNORECASE
                    ).strip()
                    lines[i] = line = ""  # remove line

            if line:
                decision.text += line + "\n"

        # always fill title
        if not title:
            decision.title = decision.text
            decision.text = ""

        decision.save()

    def notify_updated(self) -> None:
        """Notify the protocol person on the user who last updated the page"""
        pass

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
        self.notify_updated()

        self.save()

    def delete(self) -> None:
        """Delete the protocol and all related Decisions."""
        # Delete all decisions related to this protocol's page
        if self.page_id:
            decisions = Decision.get_all(selector={"page_id": self.page_id}, limit=1000)
            for decision in decisions:
                logger.info("  Deleting decision from protocol: %s", decision.title)
                decision.delete()  # Decision.delete() also removes from ChromaDB

        # Delete the protocol itself
        super().delete()
