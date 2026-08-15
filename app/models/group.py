import re
from datetime import datetime
from typing import ClassVar, Dict, List, Optional, Set

import edgy

from app.models.base import BaseDBModel, format_date
from app.models.collective_page import CollectivePage
from app.models.member_leave import is_leave_line, parse_until
from app.services.config import bot_config
from app.settings import user_regex
from app.textnorm import plain_name

# Separates a keyword from its value ("Chat-Kanäle: ..."), ignoring the
# colon of a URL scheme so a line naming a link still splits correctly.
keyword_value_regex = re.compile(r":(?!//)")


class Group(BaseDBModel):
    name: str = edgy.CharField(max_length=255, default="")
    page_id: int = edgy.BigIntegerField(unique=True)
    parent_group: str = edgy.CharField(max_length=255, null=True)
    emoji: str = edgy.CharField(max_length=32, default="")

    coordination: List[str] = edgy.JSONField(default=list)
    delegate: List[str] = edgy.JSONField(default=list)
    members: List[str] = edgy.JSONField(default=list)
    short_names: List[str] = edgy.JSONField(default=list)
    # Extra Matrix chat channels named on the page ("Chat-Kanäle: ..."), in
    # addition to the group's own channel.
    chat_channels: List[str] = edgy.JSONField(default=list)
    # Members this page marks as being on leave ("Karenz"), and the announced
    # end date per user (unix timestamp, missing = open-ended). The status
    # itself is global — `MemberLeave.sync_groups()` collects it across all
    # pages — these fields only record what this page says.
    on_leave: List[str] = edgy.JSONField(default=list)
    leave_until: Dict[str, int] = edgy.JSONField(default=dict)

    # How long the group existed. `start_date` is the first time the bot saw
    # its page, `end_date` is set when the group was retired (its page was
    # archived or deleted) — a row with an end date is kept rather than
    # deleted, so a past role still links to a group that shows who was in it.
    start_date: int | None = edgy.BigIntegerField(null=True, index=True)
    end_date: int | None = edgy.BigIntegerField(null=True, index=True)

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
    def is_active(self) -> bool:
        """Whether the group still exists (its page is neither gone nor archived)."""
        return self.end_date is None

    @property
    def start_display(self) -> str:
        return format_date(self.start_date) or ""

    @property
    def end_display(self) -> str:
        return format_date(self.end_date) or ""

    @property
    def abbreviated(self) -> str:
        max_len = 30
        return str(self)[:max_len] + ("..." if len(str(self)) > max_len else "")

    @staticmethod
    def normalize_short_names(names: List[str] | None) -> List[str]:
        """Lower-cased, deduplicated and alphabetically ordered short names."""
        return sorted({name.strip().lower() for name in names or [] if name.strip()})

    def store(self, skip_set_updated_at: bool = False) -> None:
        # Enforced here rather than only at the parsing site so no writer can
        # persist a duplicated or unsorted list.
        self.short_names = self.normalize_short_names(self.short_names)
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

    def retire(self, timestamp: int | None = None) -> None:
        """Mark the group as no longer existing, keeping everything it holds.

        The row survives with its last known membership, so a past role still
        opens a group that can say who was in it and how long it existed. Only
        the open roles are closed — like `before_remove()`, but without
        dropping the group itself. Idempotent: a group that is already retired
        keeps its original end date.
        """
        from app.models.group_role import GroupRole

        if not self.is_active:
            return

        moment = timestamp or int(datetime.now().timestamp())
        self.end_date = max(moment, self.start_date or moment)
        self.store()
        GroupRole.close_for_page(self.page_id, moment)

    @classmethod
    def invalidate_cache(cls) -> None:
        cls._cached_groups = None

    @classmethod
    def all_cached(cls) -> List["Group"]:
        """Every stored group, retired ones included."""
        if cls._cached_groups is None:
            cls._cached_groups = cls.fetch(limit=1000)
        return cls._cached_groups

    @classmethod
    def active_cached(cls) -> List["Group"]:
        """The groups that currently exist — what "the groups" means for
        everything describing the present (org chart, current roles, chat
        rooms, leave); the retired ones are only reachable by name."""
        return [group for group in cls.all_cached() if group.is_active]

    @classmethod
    def fetch_active(cls, limit: int = 1000) -> List["Group"]:
        return cls.fetch(limit=limit, end_date__isnull=True)

    @classmethod
    def get_by_name(cls, name: str) -> "Group":
        """
        Get a Group by its name case insensitive.
        If no exact match is found, try to lookup by short names.

        Retired groups stay resolvable so their dialog can be opened from a
        past role, but an active group of the same name always wins.
        """
        groups = cls.all_cached()

        for candidates in ([g for g in groups if g.is_active], groups):
            docs = [g for g in candidates if g.name.lower() == name.lower()]

            if not docs:
                # try short names
                docs = [
                    g
                    for g in candidates
                    if name.lower() in {sn.lower() for sn in g.short_names}
                ]

            if docs:
                return docs[0]

        raise ValueError(f"Group with name '{name}' not found")

    @classmethod
    def find_in_text(cls, text: str) -> Optional["Group"]:
        """The group whose name or short name occurs in `text`.

        Used to route a calendar event such as "AG Struktur Treffen" to its
        own chat channel. Matches on word boundaries (so "IT" does not match
        inside "Sitzung") and prefers the longest match, so a subgroup wins
        over the parent group it is named after. Retired groups are ignored:
        they have no chat room to route anything to.
        """
        haystack = (text or "").lower()
        if not haystack:
            return None

        best: Optional["Group"] = None
        best_length = 0

        for group in cls.active_cached():
            for candidate in [group.name, *group.short_names]:
                needle = (candidate or "").strip().lower()
                if len(needle) <= best_length:
                    continue
                if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack):
                    best = group
                    best_length = len(needle)

        return best

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

    def _mark_on_leave(
        self, usernames: List[str], until: Optional[int], open_ended: Set[str]
    ) -> None:
        """Record a leave marker seen on this page.

        A name marked twice keeps the entry that lasts longer, and a marker
        without a date beats every dated one: "Karenz" with no end is not cut
        short by a dated mention elsewhere on the same page.
        """
        for username in usernames:
            if username not in self.on_leave:
                self.on_leave = sorted(self.on_leave + [username])

            if until is None:
                open_ended.add(username)
                self.leave_until.pop(username, None)
                continue
            if username in open_ended:
                continue

            known = self.leave_until.get(username)
            if known is None or until > known:
                self.leave_until[username] = until

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

        # The page is being parsed, so the group exists: this either records
        # when it first showed up or revives a group whose page came back out
        # of the archive.
        if not self.start_date:
            self.start_date = page.timestamp or int(datetime.now().timestamp())
        self.end_date = None

        # parse content now
        lines = page.content.splitlines()
        first_word_regex = re.compile(r"\b(\w[\w-]*)\b")

        self.coordination = []
        self.delegate = []
        self.members = []
        self.short_names = []
        self.chat_channels = []
        self.on_leave = []
        self.leave_until = {}
        attr = ""
        # End date announced by the heading of a "Karenz" section, applied to
        # the names below it that do not carry one of their own.
        section_until: Optional[int] = None
        open_ended: Set[str] = set()

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
            elif first_word in bot_config.organisation.leave_person_keywords:
                # A "Karenz:" section of its own. Names below it are marked as
                # unavailable without becoming members of the group.
                attr = "on_leave"
                section_until = parse_until(
                    line, bot_config.organisation.leave_until_keywords
                )
            elif first_word in bot_config.organisation.group_shortname_keywords:
                # shortnames are split by commas. The list is rebuilt from the
                # page on every parse (it is reset above), so a name dropped
                # from the wiki disappears here too; a page naming them on
                # several lines accumulates across those lines only.
                shortnames = line.split(":")[-1].strip("*").strip().split(",")
                self.short_names = self.normalize_short_names(
                    self.short_names + shortnames
                )
                continue
            elif first_word in bot_config.organisation.group_chat_channel_keywords:
                # extra chat channels are split by commas, e.g.
                # "**Chat-Kanäle:** Fragen an AG Struktur, Termine". Entries
                # are often written as links to the existing chat — only the
                # name survives (see `plain_name`).
                # Split on the keyword's colon, not on the one in "https://".
                parts = keyword_value_regex.split(line, maxsplit=1)
                if len(parts) < 2:
                    continue
                channels = parts[1].split(",")
                channels = [name for name in map(plain_name, channels) if name]
                self.chat_channels = self.chat_channels + channels
                continue

            users = re.findall(user_regex, line)
            if users and attr:
                users_list = list(getattr(self, attr))
                users_list.extend(users)
                setattr(self, attr, sorted(users_list))

            # Leave is marked either by the section a name stands in, or
            # inline behind the name ("@anna (Karenz bis 30.06.2026)").
            if users and (
                attr == "on_leave"
                or is_leave_line(line, bot_config.organisation.leave_person_keywords)
            ):
                until = parse_until(line, bot_config.organisation.leave_until_keywords)
                if until is None and attr == "on_leave":
                    until = section_until
                self._mark_on_leave(users, until, open_ended)

            if (
                line.strip() != ""
                and not (users and attr)
                and first_word
                not in (
                    bot_config.organisation.coordination_person_keywords
                    + bot_config.organisation.delegate_person_keywords
                    + bot_config.organisation.member_person_keywords
                    + bot_config.organisation.leave_person_keywords
                )
            ):
                attr = ""
                section_until = None

        self.members = sorted(
            set(self.members) - set(self.coordination) - set(self.delegate)
        )

        self.store()
