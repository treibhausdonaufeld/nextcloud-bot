import re
from functools import cached_property
from typing import ClassVar, List, cast

from lib.nextcloud.config import bot_config
from lib.nextcloud.models.collective_page import CollectivePage
from lib.settings import user_regex

from .base import CouchDBModel


class Group(CouchDBModel):
    name: str = ""
    page_id: int
    parent_group: str | None = None
    emoji: str = ""

    coordination: List[str] = []
    delegate: List[str] = []
    members: List[str] = []
    short_names: List[str] = []

    # Class-level cache shared across all instances
    _cached_groups: ClassVar[List["Group"] | None] = None

    def build_id(self) -> str:
        return f"{self.__class__.__name__}:{self.page_id}"

    def __equal__(self, other: object) -> bool:
        if not isinstance(other, Group):
            return NotImplemented
        return self.name == other.name

    def __str__(self) -> str:
        return self.name

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Group):
            return NotImplemented
        return self.name < other.name

    @cached_property
    def all_members(self) -> List[str]:
        """Return all members, including coordination and delegates."""
        return sorted(set(self.coordination + self.delegate + self.members))

    @cached_property
    def abbreviated(self) -> str:
        max_len = 30
        return str(self)[:max_len] + ("..." if len(str(self)) > max_len else "")

    @classmethod
    def get(cls, doc_id: str) -> "Group":
        """Get a Group by its id."""
        return cast(Group, super().get(doc_id))

    @classmethod
    def get_by_name(cls, name: str) -> "Group":
        """
        Get a Group by its name case insensitive.
        If no exact match is found, try to lookup by short names.
        """

        if Group._cached_groups is None:
            Group._cached_groups = cast(List[Group], Group.get_all(limit=1000))

        docs = [g for g in Group._cached_groups if g.name.lower() == name.lower()]

        if not docs:
            # try short names
            docs = [
                g
                for g in Group._cached_groups
                if name.lower() in {sn.lower() for sn in g.short_names}
            ]

        if not docs:
            raise ValueError(f"Group with name '{name}' not found")
        return docs[0]

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

        group_names = self.valid_group_names(page.full_path)
        if len(group_names) > 1:
            self.parent_group = group_names[1]
        if not group_names:
            raise ValueError("Cannot determine group name from page")

        self.name = group_names[0]
        self.emoji = page.ocs.emoji or ""

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
            elif first_word in bot_config.organisation.group_shortname_keywords:
                # shortnames are split by commas
                shortnames = line.split(":")[-1].strip("*").strip().split(",")
                shortnames = [
                    sn.strip().lower() for sn in shortnames if sn.strip() != ""
                ]
                self.short_names.extend(sorted(shortnames))
                continue

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

        self.members = sorted(
            set(self.members) - set(self.coordination) - set(self.delegate)
        )

        self.save()
