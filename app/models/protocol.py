import logging
import re
import time
from datetime import date as dateType
from datetime import datetime
from typing import List, Optional

import edgy

from app.models.base import BaseDBModel
from app.models.collective_page import CollectivePage
from app.models.decision import Decision
from app.models.group import Group
from app.models.user import NCUserList
from app.services.config import bot_config
from app.services.notify import send_message
from app.settings import _, user_regex
from app.textnorm import strip_markdown

logger = logging.getLogger(__name__)


class Protocol(BaseDBModel):
    page_id: int = edgy.BigIntegerField(unique=True)
    # page_id of the group this protocol belongs to
    group_page_id: int | None = edgy.BigIntegerField(null=True, index=True)

    date: str = edgy.CharField(max_length=64, default="")
    # time of day the meeting took place, e.g. "18:00" (empty if unknown)
    time: str = edgy.CharField(max_length=32, default="")
    # "online" | "in_person" | "hybrid" | "" (unknown)
    location_type: str = edgy.CharField(max_length=32, default="")
    moderated_by: List[str] = edgy.JSONField(default=list)
    protocol_by: List[str] = edgy.JSONField(default=list)
    participants: List[str] = edgy.JSONField(default=list)

    # Preview text listing all agenda headings and all decisions of the
    # protocol. Computed in update_from_page(); no LLM, no external services.
    preview: str = edgy.TextField(default="")

    natural_key_fields = ("page_id",)

    class Meta:
        tablename = "protocols"

    def __str__(self) -> str:
        if self.page:
            return f"{self.page.title}"
        return f"{self.date} {self.group_name or 'No Group'}"

    @property
    def page(self) -> Optional[CollectivePage]:
        return CollectivePage.get_from_page_id_or_none(self.page_id)

    @property
    def group(self) -> Optional[Group]:
        if not self.group_page_id:
            return None
        return Group.fetch_one(page_id=self.group_page_id)

    @property
    def date_obj(self) -> dateType | None:
        if self.date:
            return datetime.strptime(self.date.split()[0], "%Y-%m-%d").date()
        return None

    @property
    def group_name(self) -> str | None:
        group = self.group
        if group:
            return group.name
        return None

    @property
    def protocol_path(self) -> str | None:
        page = self.page
        if not page:
            return None
        return page.file_path

    @property
    def attendee_count(self) -> int:
        """Approximate number of people that attended the meeting."""
        everyone = (
            set(self.participants) | set(self.moderated_by) | set(self.protocol_by)
        )
        return len(everyone)

    def compute_preview(self) -> str:
        """Build a preview text listing all agenda headings and all decisions.

        The date/group/attendee lead is intentionally omitted — that
        information is already shown on the protocol card. The preview
        contains every markdown heading found in the body (the agenda items)
        and every decision title, formatted as a bulleted list. No LLM, no
        external services — stdlib + the existing ``strip_markdown`` only.
        Returns "" when nothing usable is available.
        """
        parts: list[str] = []

        headings = self._body_headings()
        if headings:
            lines = "\n".join(f"- {h}" for h in headings)
            parts.append(f"{_('Agenda:')}\n{lines}")

        decisions = Decision.fetch(page_id=self.page_id, limit=1000)
        titles = [d.title for d in decisions if d.title]
        if titles:
            lines = "\n".join(f"- {t}" for t in titles)
            parts.append(f"{_('Decisions:')}\n{lines}")

        return "\n\n".join(parts)

    def _body_headings(self) -> list[str]:
        """Return all markdown heading texts from the protocol body.

        The header block (metadata before the first ``---`` rule or heading)
        is skipped; every ``#``-prefixed line in the remaining body is
        collected, with markdown formatting stripped from the heading text.
        """
        page = self.page
        if not page or not page.content:
            return []

        lines = page.content.splitlines()
        # Skip the header block: everything before the first --- or # heading
        # (matching update_from_page's header detection).
        start: int | None = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "---" or stripped.startswith("#"):
                start = i
                break
        if start is None:
            return []

        headings: list[str] = []
        for line in lines[start:]:
            stripped = line.strip()
            if stripped.startswith("#"):
                heading = strip_markdown(stripped.lstrip("#").strip())
                if heading:
                    headings.append(heading)
        return headings

    @staticmethod
    def extract_time(text: str) -> str:
        """Pull a ``HH:MM`` time out of a line, normalising the separator.

        Accepts ``18:00``, ``18.00`` and ``18 Uhr`` style notations and returns
        a zero-padded ``HH:MM`` string, or an empty string when none is found.
        """
        m = re.search(r"\b([01]?\d|2[0-3])[:.h]([0-5]\d)\b", text)
        if m:
            return f"{int(m.group(1)):02d}:{m.group(2)}"
        # "18 Uhr" / "18h" without minutes
        m = re.search(r"\b([01]?\d|2[0-3])\s*(?:uhr|h)\b", text, flags=re.IGNORECASE)
        if m:
            return f"{int(m.group(1)):02d}:00"
        return ""

    @staticmethod
    def detect_location_type(header_lines: List[str]) -> str:
        """Classify a meeting as online / in_person / hybrid from its header.

        Prefers lines introduced by a location keyword (e.g. ``Ort: ...``) so
        the online/in-person hints are read from the location field rather than
        unrelated header text. Falls back to scanning the whole header block
        when no such line is present.
        """
        location_kws = bot_config.organisation.meeting_location_keywords
        first_word_regex = re.compile(r"\b(\w[\w-]*)\b")
        location_lines = [
            line
            for line in header_lines
            if (m := first_word_regex.search(line))
            and m.group(1).lower() in location_kws
        ]

        blob = "\n".join(location_lines or header_lines).lower()
        online = any(
            kw in blob for kw in bot_config.organisation.online_meeting_keywords
        )
        in_person = any(
            kw in blob for kw in bot_config.organisation.in_person_meeting_keywords
        )
        if online and in_person:
            return "hybrid"
        if online:
            return "online"
        if in_person:
            return "in_person"
        return ""

    @classmethod
    def valid_date(cls, title: str) -> bool:
        """Check if the given title is a valid protocol title."""
        # Simple check: title starts with a date in YYYY-MM-DD format
        if " " not in title:
            return False
        date_str, _group_name = title.split(" ", 1)
        # parse date_str and check if valid date
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return False
        return True

    @classmethod
    def is_valid_protocol_title(cls, title: str) -> bool:
        """Check if the given title corresponds to a valid protocol title."""

        def is_valid_group_name(name: str) -> bool:
            try:
                Group.get_by_name(name)  # check if group exists
            except ValueError:
                # Group doesn't exist, check if title would still be valid from extra_groups
                extra_groups = bot_config.organisation.extra_groups
                if name.upper() not in extra_groups.keys() and all(
                    name.upper() not in names for names in extra_groups.values()
                ):
                    return False
            return True

        _date_str, protocol_group = title.split(" ", 1)

        check = is_valid_group_name(protocol_group)
        while not check and " " in protocol_group:
            # try removing last word
            protocol_group = " ".join(protocol_group.split(" ")[:-1])
            check = is_valid_group_name(protocol_group)

        return check and cls.valid_date(title)

    @classmethod
    def is_protocol_page(cls, page: CollectivePage) -> bool:
        protocol_kws = set(bot_config.organisation.protocol_subtype_keywords)

        return (
            len(page.file_path.split("/")) > 1
            and (
                page.is_readme and page.file_path.split("/")[-2].lower() in protocol_kws
            )
            or (
                not page.is_readme
                and page.file_path.split("/")[-1].lower() in protocol_kws
            )
        )

    def extract_decisions(self) -> List[Decision]:
        """Get all decisions marked with ::: success"""
        page = self.page
        if not page or not page.content:
            return []

        if (
            self.valid_date(page.title)
            and self.date_obj
            and self.date_obj > datetime.now().date()
        ):
            logger.info(
                "Skipping decision extraction for future protocol page %s",
                self.page_id,
            )
            return []

        # delete existing decision for this page
        for d in Decision.fetch(page_id=self.page_id):
            d.remove()

        # Simple regex to find ::: success blocks
        decisions: List[Decision] = []
        for match in re.finditer(r"::: success(.*?):::", page.content, re.DOTALL):
            context = self.heading_before(page.content, match.start())
            decision: Decision | None = self.save_decision(
                match.group(1), context=context
            )
            if decision is not None:
                decisions.append(decision)
        return decisions

    @staticmethod
    def heading_before(content: str, position: int) -> str:
        """Return the nearest markdown heading above `position` (agenda item)."""
        for line in reversed(content[:position].splitlines()):
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return ""

    def save_decision(self, block: str, context: str = "") -> Decision | None:
        """Parse and save on decision from a markdown block."""

        def clean_line(line: str) -> str:
            return (
                line.replace("**", "")
                .replace("__", "")
                .strip("\\")
                .strip("\n")
                .strip("\r")
            )

        lines = block.strip().splitlines()
        if not lines:
            return None

        title = clean_line(lines[0])
        for title_kw in bot_config.organisation.decision_title_keywords:
            title = clean_line(
                re.sub(rf"^{title_kw}[:\s\-]*", "", title, flags=re.IGNORECASE)
                .strip(":")
                .strip()
            )
        # remove first line
        lines = lines[1:]

        if bot_config.organisation.protocol_decision_example_title in title:
            return None  # skip example decisions

        group = self.group
        decision = Decision(
            title=title,
            date=self.date,
            page_id=self.page_id,
            group_name=group.name if group else "",
            context=context,
        )

        # iterate over all lines and check each line for keywords
        for i, line in enumerate(lines):
            line = clean_line(line)

            for valid_until_kw in bot_config.organisation.decision_valid_until_keywords:
                if re.match(rf"^{valid_until_kw}[:\s\-]*", line, flags=re.IGNORECASE):
                    decision.valid_until = clean_line(
                        re.sub(
                            rf"^{valid_until_kw}[:\s\-]*", "", line, flags=re.IGNORECASE
                        )
                    )
                    line = ""  # remove line after processing

            for objection_kw in bot_config.organisation.decision_objection_keywords:
                if re.match(rf"^{objection_kw}[:\s\-]*", line, flags=re.IGNORECASE):
                    decision.objections = clean_line(
                        re.sub(
                            rf"^{objection_kw}[:\s\-]*", "", line, flags=re.IGNORECASE
                        )
                    )
                    if len(lines) > i + 1:
                        # add all following lines as objections too
                        decision.objections += "\n".join(
                            [clean_line(last_lines) for last_lines in lines[i + 1 :]]
                        )

            if decision.objections:
                break  # stop processing lines after objections were set

            decision.text += line + "\n"

        # always fill title
        if not title:
            decision.title = decision.text
            decision.text = ""

        decision.store()
        return decision

    def notify_updated(self, decisions: List[Decision]) -> None:
        """Notify the protocol person on the user who last updated the page"""
        page = self.page
        username = (
            self.protocol_by[0]
            if self.protocol_by
            else (page.last_user_id if page else None)
        )
        if not username:
            logger.warning("Cannot notify updated: no username found for protocol")

        corrections = []
        if not self.moderated_by:
            corrections.append(_("No person listed for moderations"))
        if not self.protocol_by:
            corrections.append(_("No person listed for protocol"))
        if not self.participants:
            corrections.append(_("No participants listed"))

        # check that title starts with date
        if not self.valid_date(page.title if page else ""):
            corrections.append(_("Title must have a valid date in 'YYYY-MM-DD' format"))
        if not self.is_valid_protocol_title(page.title if page else ""):
            corrections.append(_("Title must be 'YYYY-MM-DD Group Name'"))

        if (
            page
            and page.content
            and bot_config.organisation.protocol_template_keyword
            in set(page.content.splitlines())
        ):
            corrections.append(
                _("Protocol contains a '{template}' section. Please remove it!").format(
                    template=bot_config.organisation.protocol_template_keyword
                )
            )

        user_list = NCUserList()
        user = user_list.get_user_by_uid(username or "")
        displayname = user.displayname if user else username
        # Rocket.Chat knows users by their authentik username, not the
        # Nextcloud uid — address the DM accordingly.
        chat_handle = user_list.chat_username(username) if username else username

        if corrections:
            message = _(
                "Hello {displayname},\n\n"
                "The protocol [{protocol}]({url}) looks generally fine, but there are some issues:\n\n- {issues}\n\n"
                "Please fix them when you edit the protocol the next time (no hurry, take your time!). Thank you!"
            ).format(
                displayname=displayname,
                protocol=str(self),
                url=(page.url if page else ""),
                issues="\n- ".join(corrections),
            )
        else:
            # generate a message to the user to praise how well the document is written
            message = _(
                "Hello {displayname},\n\n"
                "The protocol [{protocol}]({url}) looks great! Thank you for the careful work.\n\n"
            ).format(
                displayname=displayname,
                protocol=str(self),
                url=(page.url if page else ""),
            )

        message += "\n---\n\n"
        message += _("Date: ") + self.date + "\n"
        if page and page.last_user_id:
            message += (
                _("Last update by: ") + user_list.display_name(page.last_user_id) + "\n"
            )
        message += (
            _("Moderated by: ")
            + ", ".join(user_list.display_names(self.moderated_by))
            + "\n"
        )
        message += (
            _("Protocol by: ")
            + ", ".join(user_list.display_names(self.protocol_by))
            + "\n"
        )
        message += (
            _("Participants: ")
            + ", ".join(user_list.display_names(self.participants))
            + "\n"
        )
        if decisions:
            message += _("Decisions made:\n")
            for decision in decisions:
                message += f"- ✅ **{decision.title}**"
                if decision.text:
                    message += "\r  " + decision.text
                if decision.objections:
                    message += "\r  **" + _("Objections") + "**: " + decision.objections
                if decision.valid_until:
                    message += (
                        "\r  **" + _("Valid until") + "**: " + decision.valid_until
                    )
                message += "\n"

        send_message(text=message, channel=f"@{chat_handle}")

        if not corrections:
            text = _("Please manually a post in the channel #{protocols}").format(
                protocols=bot_config.organisation.protocol_channel_name
            )
            send_message(text=text, channel=f"@{chat_handle}")

    def update_from_page(self) -> None:
        """
        Update the Protocol fields from the associated CollectivePage content.
        Parses the page content to extract moderated_by, protocol_by, participants,
        and updates the date and group_page_id if possible.
        """
        page = self.page
        if not page or not page.content:
            raise ValueError("Cannot update Group: page content is missing")

        # return early if not cooled down yet
        # page.timestamp may be a non-numeric value in tests (e.g. a Mock). Coerce to float
        # and fall back to skipping the cooldown check if that fails to avoid TypeError.
        page_timestamp = None
        try:
            page_timestamp = (
                float(page.timestamp) if page.timestamp is not None else None
            )
        except Exception:
            page_timestamp = None

        if (
            page_timestamp is not None
            and (time.time() - page_timestamp)
            < bot_config.organisation.protocol_cooldown_minutes * 60
        ):
            # mark the page so the next worker iteration picks it up again
            page.updated_at = 1
            page.store(skip_set_updated_at=True)

            logger.info(
                "Skipping protocol update for page %s: cooldown period not yet passed",
                self.page_id,
            )
            return

        if self.valid_date(page.title):
            self.date = page.title.split(" ")[0]  # first word as date

        try:
            self.group_page_id = Group.get_for_page(page).page_id
        except ValueError:
            # could not determine group id from path of page, try to get from title
            group_name = " ".join(page.title.split(" ")[1:])
            try:
                group = Group.get_by_name(group_name)
                self.group_page_id = group.page_id
            except ValueError:
                pass

        lines = page.content.splitlines()
        first_word_regex = re.compile(r"\b(\w[\w-]*)\b")

        self.moderated_by = []
        self.protocol_by = []
        self.participants = []
        self.time = ""
        attr = ""
        header_lines: list[str] = []

        for line in lines:
            if line.strip() == "---" or line.strip().startswith("#"):
                break  # stop at horizontal rule

            header_lines.append(line)

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

            if first_word in bot_config.organisation.meeting_time_keywords:
                found_time = self.extract_time(line)
                if found_time:
                    self.time = found_time

            users = re.findall(user_regex, line)
            if users and attr:
                users_list = list(getattr(self, attr))
                users_list.extend(users)
                setattr(self, attr, sorted(users_list))
            elif line.strip() != "" and first_word not in (
                bot_config.organisation.moderation_person_keywords
                + bot_config.organisation.protocol_person_keywords
                + bot_config.organisation.participant_person_keywords
            ):
                attr = ""

        self.location_type = self.detect_location_type(header_lines)

        self.participants = sorted(
            set(self.participants) - set(self.moderated_by) - set(self.protocol_by)
        )
        try:
            decisions = self.extract_decisions()
            self.preview = self.compute_preview()

            # Only notify if protocol is recent
            if self.date_obj:
                days_old = (datetime.now().date() - self.date_obj).days
                if (
                    days_old >= 0
                    and days_old <= bot_config.organisation.protocol_max_age_days
                ):
                    self.notify_updated(decisions)
                else:
                    logger.info(
                        "Skipping notification for protocol page %s: date is %d days old (must be < %d)",
                        self.page_id,
                        days_old,
                        bot_config.organisation.protocol_max_age_days,
                    )

            self.store()
        except ValueError as e:
            logger.error("Error updating protocol from page: %s", e)

    def before_remove(self) -> None:
        """Delete all decisions related to this protocol's page."""
        if self.page_id:
            decisions = Decision.fetch(page_id=self.page_id, limit=1000)
            for decision in decisions:
                logger.info("  Deleting decision from protocol: %s", decision.title)
                decision.remove()
