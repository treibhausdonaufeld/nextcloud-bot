"""Leave ("Karenz"): who is currently unavailable, and for how long.

Being on leave is a property of the *person*, not of a single group: whoever
is marked anywhere counts as unavailable in every group they belong to. The
marker is written on a group page in the wiki, either as a section of its own

    **Karenz:** mention://user/anna bis 30.06.2026

or inline behind a member's mention in one of the role sections

    - mention://user/anna (Karenz bis 30.06.2026)

Both forms are keyword driven (`organisation.leave_person_keywords` and
`organisation.leave_until_keywords`), so which words count is configuration.

Like `GroupRole` this table keeps the history: a row records when a leave
started, when it was announced to end (`until_date`) and when it actually
ended (`end_date`). A leave ends either because the announced date passed or
because the marker disappeared from the wiki, whichever comes first — so a
leave that nobody bothers to remove still expires on its own.
"""

import logging
import re
from datetime import datetime, time
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import edgy
import pytz

from app.models.base import BaseDBModel, format_date
from app.settings import settings

if TYPE_CHECKING:
    from app.models.group import Group

logger = logging.getLogger(__name__)

# "30.06.2026" and "2026-06-30"; both are common in the wiki pages.
_DATE_PATTERNS: Tuple[Tuple[re.Pattern, Tuple[int, int, int]], ...] = (
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), (1, 2, 3)),
    (re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b"), (3, 2, 1)),
)


def _keyword_regex(keywords: List[str]) -> Optional[re.Pattern]:
    """Whole-word alternation over the configured keywords."""
    parts = [re.escape(kw.strip().lower()) for kw in keywords if kw.strip()]
    if not parts:
        return None
    # \b does not fire after a trailing "." or ":" inside a keyword, so guard
    # the edges with lookarounds on word characters instead.
    return re.compile(rf"(?<!\w)(?:{'|'.join(parts)})(?!\w)")


def end_of_day(year: int, month: int, day: int) -> Optional[int]:
    """Unix timestamp of the last second of that day, in the local timezone.

    A leave "bis 30.06.2026" includes the 30th, so the announced date is
    stored as the end of that day rather than its start.
    """
    try:
        naive = datetime.combine(datetime(year, month, day).date(), time(23, 59, 59))
    except ValueError:  # e.g. 31.02.2026
        return None
    tz = pytz.timezone(settings.timezone)
    return int(tz.localize(naive).timestamp())


def parse_until(text: str, keywords: List[str]) -> Optional[int]:
    """The end date announced in `text`, as a unix timestamp.

    Only dates behind one of the `leave_until_keywords` count, so the date in
    "Karenz seit 01.01.2026" is not mistaken for an end date.
    """
    lowered = (text or "").lower()
    if not lowered:
        return None

    pattern = _keyword_regex(keywords)
    if pattern is None:
        return None
    match = pattern.search(lowered)
    if not match:
        return None

    tail = lowered[match.end() :]
    for date_pattern, (y, m, d) in _DATE_PATTERNS:
        found = date_pattern.search(tail)
        if found:
            return end_of_day(
                int(found.group(y)), int(found.group(m)), int(found.group(d))
            )
    return None


def is_leave_line(text: str, keywords: List[str]) -> bool:
    """Whether `text` carries one of the leave keywords."""
    pattern = _keyword_regex(keywords)
    return bool(pattern and pattern.search((text or "").lower()))


class MemberLeave(BaseDBModel):
    username: str = edgy.CharField(max_length=255, index=True)
    # where the leave was announced; kept for reference only, the status
    # itself is global
    group_name: str = edgy.CharField(max_length=255, default="")
    page_id: int | None = edgy.BigIntegerField(null=True, index=True)

    # unix timestamps
    start_date: int = edgy.BigIntegerField(index=True)
    # announced end ("Karenz bis ..."), None while the leave is open-ended
    until_date: int | None = edgy.BigIntegerField(null=True, index=True)
    # when the leave actually ended, None while it is running
    end_date: int | None = edgy.BigIntegerField(null=True, index=True)

    class Meta:
        tablename = "member_leaves"

    def __str__(self) -> str:
        return (
            f"MemberLeave({self.username} {self.start_display}-"
            f"{self.end_display or self.until_display or ''})"
        )

    # --- display ------------------------------------------------------------

    def is_current(self, now: int | None = None) -> bool:
        """Running right now: not ended, and the announced date has not passed.

        The expiry is evaluated on read as well as during the sync, so a leave
        looks over on the very day it ends even if no sync has run since.
        """
        if self.end_date is not None:
            return False
        moment = now if now is not None else int(datetime.now().timestamp())
        return self.until_date is None or self.until_date >= moment

    @property
    def start_display(self) -> str:
        return format_date(self.start_date) or ""

    @property
    def until_display(self) -> str | None:
        return format_date(self.until_date)

    @property
    def end_display(self) -> str | None:
        return format_date(self.end_date)

    # --- queries ------------------------------------------------------------

    @classmethod
    def for_user(cls, username: str) -> List["MemberLeave"]:
        """All leaves of one member, newest first."""
        return cls.fetch(username=username, limit=10000, order_by="-start_date")

    @classmethod
    def open_rows(cls) -> List["MemberLeave"]:
        """Rows that have not been closed yet (the announced end may have passed)."""
        return cls.fetch(limit=10000, order_by="-start_date", end_date__isnull=True)

    @classmethod
    def all_rows(cls) -> List["MemberLeave"]:
        return cls.fetch(limit=100000, order_by="-start_date")

    @classmethod
    def current_by_user(cls, now: int | None = None) -> Dict[str, "MemberLeave"]:
        """`username -> leave` for everybody who is unavailable right now."""
        moment = now if now is not None else int(datetime.now().timestamp())
        current: Dict[str, MemberLeave] = {}
        for row in cls.open_rows():
            if not row.is_current(moment):
                continue
            known = current.get(row.username)
            # An open-ended leave outlasts a dated one.
            if known is None or _outlasts(row, known):
                current[row.username] = row
        return current

    # --- history bookkeeping ------------------------------------------------

    @classmethod
    def desired_from_groups(
        cls, groups: List["Group"]
    ) -> Dict[str, Tuple[int | None, str, int | None]]:
        """`username -> (until, group name, page id)` over all group pages.

        A member marked on several pages is on leave once; the entry that
        lasts longest wins, so removing the marker from one page does not cut
        a leave that another page still announces.
        """
        desired: Dict[str, Tuple[int | None, str, int | None]] = {}
        for group in groups:
            until_by_user = dict(getattr(group, "leave_until", None) or {})
            for username in getattr(group, "on_leave", None) or []:
                until = until_by_user.get(username)
                known = desired.get(username)
                if known is not None and not _later(until, known[0]):
                    continue
                desired[username] = (until, group.name, group.page_id)
        return desired

    @classmethod
    def sync_groups(
        cls,
        groups: List["Group"],
        timestamps: Dict[int, int] | None = None,
        now: int | None = None,
    ) -> None:
        """Reconcile the stored leaves with what the group pages say.

        Runs over *all* groups rather than the changed page alone, because the
        status is global: dropping the marker from the one page that carried
        it ends the leave, no matter which page was edited.

        Idempotent — re-parsing every page neither duplicates a running leave
        nor restarts one that has already expired.
        """
        moment = now if now is not None else int(datetime.now().timestamp())
        page_times = timestamps or {}
        desired = cls.desired_from_groups(groups)

        rows = cls.all_rows()
        latest: Dict[str, MemberLeave] = {}
        for row in rows:
            known = latest.get(row.username)
            if known is None or row.start_date > known.start_date:
                latest[row.username] = row

        for row in rows:
            if row.end_date is not None:
                continue

            wanted = desired.get(row.username)
            if wanted is None:
                # The marker is gone from the wiki. An announced date that has
                # already passed is the truer end than the day we noticed.
                until = row.until_date
                end = until if until is not None and until < moment else moment
                row.end_date = max(end, row.start_date)
                row.store()
                logger.info("Ended leave %s", row)
                continue

            until = wanted[0]
            if until != row.until_date:
                # The wiki is the truth: an extended or shortened leave keeps
                # its start date and just moves its end.
                row.until_date = until
                row.store()
                logger.info("Updated leave %s", row)

            if row.until_date is not None and row.until_date < moment:
                # Still marked, but the announced end has passed.
                row.end_date = max(row.until_date, row.start_date)
                row.store()
                logger.info("Expired leave %s", row)

        for username, (until, group_name, page_id) in sorted(desired.items()):
            known = latest.get(username)
            if known is not None and known.end_date is None:
                continue  # handled above
            if (
                known is not None
                and known.until_date == until
                and known.end_date is not None
                and until is not None
                and known.end_date >= until
            ):
                # Already served: this leave ran its course, the page just
                # still carries the (now historic) marker.
                continue

            start = page_times.get(page_id or -1, moment)
            if until is not None and until < start:
                # A leave that was already over when it was written down.
                start = min(start, until)

            row = cls(
                username=username,
                group_name=group_name,
                page_id=page_id,
                start_date=start,
                until_date=until,
            )
            if until is not None and until < moment:
                row.end_date = max(until, start)
            row.store()
            logger.info("Started leave %s", row)


def _later(candidate: int | None, current: int | None) -> bool:
    """Whether `candidate` ends later than `current` (None = open-ended)."""
    if candidate is None:
        return current is not None
    if current is None:
        return False
    return candidate > current


def _outlasts(candidate: "MemberLeave", current: "MemberLeave") -> bool:
    return _later(candidate.until_date, current.until_date)
