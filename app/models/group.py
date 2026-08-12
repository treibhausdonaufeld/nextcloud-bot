import re
from typing import ClassVar, List

import edgy

from app.models.base import BaseDBModel
from app.models.collective_page import CollectivePage
from app.services.config import bot_config
from app.settings import user_regex


class Group(BaseDBModel):
    name: str = edgy.CharField(max_length=255, default="")
    page_id: int = edgy.BigIntegerField(unique=True)
    parent_group: str = edgy.CharField(max_length=255, null=True)
    emoji: str = edgy.CharField(max_length=32, default="")

    coordination: List[str] = edgy.JSONField(default=list)
    delegate: List[str] = edgy.JSONField(default=list)
    members: List[str] = edgy.JSONField(default=list)
    short_names: List[str] = edgy.JSONField(default=list)

    natural_key_fields = ("page_id",)

    # Class-level cache shared across all instances
    _cached_groups: ClassVar[List["Group"] | None] = None

    class Meta:
        tablename = "groups"

    def __str__(self) -> str:
        return self.name

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Group):
            return NotImplemented
        return self.name < other.name

    @property
    def all_members(self) -> List[str]:
        """Return all members, including coordination and delegates."""
        return sorted(set(self.coordination + self.delegate + self.members))

    @property
    def abbreviated(self) -> str:
        max_len = 30
        return str(self)[:max_len] + ("..." if len(str(self)) > max_len else "")

    def store(self, skip_set_updated_at: bool = False) -> None:
        super().store(skip_set_updated_at=skip_set_updated_at)
        Group.invalidate_cache()

    def remove(self) -> None:
        super().remove()
        Group.invalidate_cache()

    def before_remove(self) -> None:
        # The group is gone, so nobody holds a role in it any more; the
        # history itself is kept.
        from app.models.group_role import GroupRole

        GroupRole.close_for_page(self.page_id)

    @classmethod
    def invalidate_cache(cls) -> None:
        cls._cached_groups = None

    @classmethod
    def all_cached(cls) -> List["Group"]:
        if cls._cached_groups is None:
            cls._cached_groups = cls.fetch(limit=1000)
        return cls._cached_groups

    @classmethod
    def get_by_name(cls, name: str) -> "Group":
        """
        Get a Group by its name case insensitive.
        If no exact match is found, try to lookup by short names.
        """
        groups = cls.all_cached()

        docs = [g for g in groups if g.name.lower() == name.lower()]

        if not docs:
            # try short names
            docs = [
                g
                for g in groups
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
            or upper_name in bot_config.organisation.extra_groups.keys()
        )

    @staticmethod
    def valid_group_names(file_path: str) -> List[str]:
        """Extract valid group names from the given file path."""
        path_parts = file_path.split("/")
        return [name for name in reversed(path_parts) if Group.valid_name(name)]

    @staticmethod
    def is_archived_path(path: str) -> bool:
        """Whether any path segment marks the page as archived.

        Group pages are archived by moving them below a page such as
        "Archiv" (see `organisation.archive_page_names`), which shows up as a
        segment of every affected page's path — subpages included, so a whole
        branch is archived at once.
        """
        names = [
            name.strip().lower()
            for name in bot_config.organisation.archive_page_names
            if name.strip()
        ]

        for segment in path.split("/"):
            segment = segment.strip().lower()
            for name in names:
                # "Archiv", "Archiv 2024" and "Archiv-2024" all count; a page
                # like "Archivierung" does not.
                if segment == name:
                    return True
                if segment.startswith(name) and segment[len(name) : len(name) + 1] in (
                    " ",
                    "-",
                    "_",
                ):
                    return True
        return False

    @classmethod
    def get_for_page(cls, page: CollectivePage) -> "Group":
        """Extract the group info from the given page."""
        group_names = Group.valid_group_names(page.file_path)
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
        self.emoji = page.emoji or ""

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
                self.short_names = self.short_names + sorted(shortnames)
                continue

            users = re.findall(user_regex, line)
            if users and attr:
                users_list = list(getattr(self, attr))
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

        self.store()
