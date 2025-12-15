import logging
import re
from datetime import date as dateType
from datetime import datetime
from functools import cached_property
from typing import List

from google import genai

from lib.nextcloud.config import bot_config
from lib.nextcloud.models.decision import Decision
from lib.nextcloud.models.group import Group
from lib.nextcloud.models.user import NCUserList
from lib.outbound.rocketchat import send_message
from lib.settings import _, settings, user_regex

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

    summary_posted: bool = False  # not used atm
    ai_summary: str = ""

    def build_id(self) -> str:
        return f"{self.__class__.__name__}:{self.page_id}"

    def __str__(self) -> str:
        return f"{self.date} {self.group_name or 'No Group'}"

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
        _date_str, group_name = title.split(" ", 1)
        try:
            Group.get_by_name(group_name)  # check if group exists
        except ValueError:
            # Group doesn't exist, check if title would still be valid from extra_groups
            extra_groups = bot_config.organisation.extra_groups
            if group_name.upper() not in extra_groups.keys() and all(
                group_name.upper() not in names for names in extra_groups.values()
            ):
                return False

        return True and cls.valid_date(title)

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

    def extract_decisions(self) -> List[Decision]:
        """Get all decisions marked with ::: success"""
        if not self.page or not self.page.content:
            return []

        if (
            self.valid_date(self.page.title)
            and self.date_obj
            and self.date_obj > datetime.now().date()
        ):
            logger.info(
                "Skipping decision extraction for future protocol %s", self.build_id()
            )
            return []

        # delete existing decision for this page
        for d in Decision.get_all(selector={"page_id": self.page_id}):
            d.delete()

        # Simple regex to find ::: success blocks
        decision_blocks = re.findall(
            r"::: success(.*?):::", self.page.content, re.DOTALL
        )

        decisions: List[Decision] = []
        for block in decision_blocks:
            decision: Decision | None = self.save_decision(block)
            if decision is not None:
                decisions.append(decision)
        return decisions

    def save_decision(self, block: str) -> Decision | None:
        """Parse and save on decision from a markdown block."""

        def clean_line(line: str) -> str:
            return (
                line.replace("**", "")
                .replace("__", "")
                .strip("*")
                .strip("_")
                .strip("\n")
                .strip("\r")
                .strip()
            )

        lines = block.strip().splitlines()
        if not lines:
            return None

        title = clean_line(lines[0])
        for title_kw in bot_config.organisation.decision_title_keywords:
            title = (
                re.sub(rf"^{title_kw}[:\s\-]*", "", title, flags=re.IGNORECASE)
                .strip(":")
                .strip()
            )
        lines[0] = ""  # remove title line

        if bot_config.organisation.protocol_decision_example_title in title:
            return None  # skip example decisions

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
            if not line or decision.objections:
                # skip all lines if objections already found
                continue

            for valid_until_kw in bot_config.organisation.decision_valid_until_keywords:
                if re.match(rf"^{valid_until_kw}[:\s\-]*", line, flags=re.IGNORECASE):
                    decision.valid_until = clean_line(
                        re.sub(
                            rf"^{valid_until_kw}[:\s\-]*", "", line, flags=re.IGNORECASE
                        )
                    )
                    lines[i] = line = ""  # remove line

            for objection_kw in bot_config.organisation.decision_objection_keywords:
                if re.match(rf"^{objection_kw}[:\s\-]*", line, flags=re.IGNORECASE):
                    decision.objections = clean_line(
                        re.sub(
                            rf"^{objection_kw}[:\s\-]*", "", line, flags=re.IGNORECASE
                        )
                    )
                    if len(lines) > i + 1:
                        # add all following lines as objections too
                        decision.objections += " ".join(
                            [clean_line(last_lines) for last_lines in lines[i + 1 :]]
                        )
                    lines[i] = line = ""  # remove line

            if line:
                decision.text += line + " "

        # always fill title
        if not title:
            decision.title = decision.text
            decision.text = ""

        decision.save()
        return decision

    def notify_updated(self, decisions: List[Decision]) -> None:
        """Notify the protocol person on the user who last updated the page"""
        username = (
            self.protocol_by[0]
            if self.protocol_by
            else (self.page.ocs.lastUserId if self.page and self.page.ocs else None)
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
        if not self.valid_date(self.page.title if self.page else ""):
            corrections.append(_("Title must have a valid date in 'YYYY-MM-DD' format"))
        if not self.is_valid_protocol_title(self.page.title if self.page else ""):
            corrections.append(_("Title must be 'YYYY-MM-DD Group Name'"))

        if (
            self.page
            and self.page.content
            and bot_config.organisation.protocol_template_keyword
            in set(self.page.content.splitlines())
        ):
            corrections.append(
                _("Protocol contains a '{template}' section. Please remove it!").format(
                    template=bot_config.organisation.protocol_template_keyword
                )
            )

        user = NCUserList().get_user_by_uid(username or "")
        displayname = user.ocs.displayname if user else username

        if corrections:
            message = _(
                "Hello {displayname},\n\n"
                "The protocol [{protocol}]({url}) looks generally fine, but there are some issues:\n\n- {issues}\n\n"
                "Please fix them when you edit the protocol the next time (no hurry, take your time!). Thank you!"
            ).format(
                displayname=displayname,
                protocol=str(self),
                url=(self.page.url if self.page else ""),
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
                url=(self.page.url if self.page else ""),
            )

        message += "\n---\n\n"
        message += _("Date: ") + self.date + "\n"
        if self.page and self.page.ocs and self.page.ocs.lastUserId:
            message += _("Last update by: ") + self.page.ocs.lastUserId + "\n"
        message += _("Moderated by: ") + ", ".join(self.moderated_by) + "\n"
        message += _("Protocol by: ") + ", ".join(self.protocol_by) + "\n"
        message += _("Participants: ") + ", ".join(self.participants) + "\n"
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
        if self.ai_summary:
            message += _("AI Summary:") + "\n" + self.ai_summary + "\n\n"

        send_message(text=message, channel=f"@{username}")

        if not corrections:
            text = _("Please manually a post in the channel #{protocols}").format(
                protocols=bot_config.organisation.protocol_channel_name
            )
            send_message(text=text, channel=f"@{username}")

        # self.summary_posted = True

        # message = (
        #     _(
        #         "When you're ok with these changes, then nothing else is needed from your side."
        #     )
        #     + "\n"
        #     + _("I will post this information to the channel #{protocols}").format(
        #         protocols=bot_config.organisation.protocol_channel_name
        #     )
        # )
        # send_message(text=message, channel=f"@{username}")

        # message = (
        #     f"## {self}\n"
        #     + f"{self.page.url if self.page else ''}\n\n"
        #     + self.ai_summary
        # )
        # if decisions:
        #     message += "\n\n" + _("Decisions made:\n")
        #     for decision in decisions:
        #         message += f"- ✅ {decision.title}\n"
        # send_message(
        #     text=message, channel=bot_config.organisation.protocol_channel_name
        # )
        # self.summary_posted = True

    def generate_ai_summary(self) -> None:
        # Generate AI summary of the protocol content
        if self.page and self.page.content and settings.gemini_api_key:
            try:
                logger.info("Generating AI summary for protocol %s", self.build_id())

                prompt_template = (
                    "Summarize the following protocol in 2-6 concise sentences."
                    " Focus on the most important topics, decisions and outcomes. "
                    f"Make sure to use the language {settings.default_language}. "
                    "Don't halucinate and make things up which are not in the original text\n\n"
                    "Protocol from {date}:\n{content}\n\nSummary:"
                )
                prompt = prompt_template.format(
                    date=self.date, content=self.page.content
                )

                client = genai.Client(api_key=settings.gemini_api_key)
                response = client.models.generate_content(
                    model=settings.gemini_model,
                    contents=prompt,
                )

                if response and response.text:
                    self.ai_summary = response.text.strip()
                    logger.info("AI summary generated successfully")
                else:
                    logger.warning("AI summary generation returned empty response")
            except Exception as e:
                logger.error("Failed to generate AI summary: %s", e)
                self.ai_summary = ""
        else:
            if not settings.gemini_api_key:
                logger.info("Skipping AI summary: no Gemini API key configured")
            elif not self.page or not self.page.content:
                logger.info("Skipping AI summary: no page content available")

    def update_from_page(self) -> None:
        page = self.page
        if not page or not page.content:
            raise ValueError("Cannot update Group: page content is missing")

        if self.valid_date(page.title):
            self.date = page.title.split(" ")[0]  # first word as date

        try:
            self.group_id = Group.get_for_page(page).id
        except ValueError:
            # could not determine group id from path of page, try to get from title
            group_name = " ".join(page.title.split(" ")[1:])
            try:
                group = Group.get_by_name(group_name)
                self.group_id = group.id
            except ValueError:
                pass

        lines = page.content.splitlines()
        first_word_regex = re.compile(r"\b(\w[\w-]*)\b")

        self.moderated_by = []
        self.protocol_by = []
        self.participants = []
        attr = ""

        for line in lines:
            if line.strip() == "---" or line.strip().startswith("#"):
                break  # stop at horizontal rule

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
        try:
            decisions = self.extract_decisions()
            self.generate_ai_summary()

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
                        "Skipping notification for protocol %s: date is %d days old (must be < %d)",
                        self.build_id(),
                        days_old,
                        bot_config.organisation.protocol_max_age_days,
                    )

            self.save()
        except ValueError as e:
            logger.error("Error updating protocol from page: %s", e)

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
