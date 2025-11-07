from __future__ import annotations

import logging
import re
import threading
import time
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from lib.nextcloud.models.collective_page import CollectivePage
from lib.settings import settings

logger = logging.getLogger(__name__)


class OrganisationConfig(BaseModel):
    group_prefixes: List[str] = Field(default_factory=lambda: ["AG", "UG", "PG"])
    top_group_name: str = "Koordinationskreis"
    extra_groups: List[str] = Field(default_factory=list)
    protocol_subtype_keywords: List[str] = Field(
        default_factory=lambda: ["protocol", "protocols", "protokoll", "protokolle"]
    )

    decision_title_keywords: List[str] = Field(
        default_factory=lambda: ["entscheidung", "decision", "beschluss", "resolution"]
    )
    decision_valid_until_keywords: List[str] = Field(
        default_factory=lambda: ["gültig bis", "valid until", "befristet auf"]
    )
    decision_objection_keywords: List[str] = Field(
        default_factory=lambda: ["einwände", "objections", "einwand"]
    )

    protocol_person_keywords: List[str] = Field(
        default_factory=lambda: [
            "protokollant",
            "protokollantin",
            "protokoll",
            "protokollant:in",
        ]
    )
    moderation_person_keywords: List[str] = Field(
        default_factory=lambda: [
            "moderation",
            "moderator",
            "moderatorin",
            "moderator:in",
        ]
    )
    participant_person_keywords: List[str] = Field(
        default_factory=lambda: [
            "teilnehmer",
            "teilnehmende",
            "teilnehmerin",
            "teilnehmerinnen",
        ]
    )

    coordination_person_keywords: List[str] = Field(
        default_factory=lambda: [
            "koordination",
            "koordinator",
            "koordinatorin",
            "koordinator:in",
            "sprecher",
            "sprecherin",
            "sprecher:in",
        ]
    )
    delegate_person_keywords: List[str] = Field(
        default_factory=lambda: ["delegierter", "delegierte"]
    )
    member_person_keywords: List[str] = Field(
        default_factory=lambda: ["mitglied", "mitglieder"]
    )

    @field_validator("group_prefixes", "extra_groups", mode="before")
    def to_upper(cls, v):
        return [prefix.upper() for prefix in v]


class AvatarConfig(BaseModel):
    fetch_avatar: bool = True
    avatar_folder: str = "/avatare"
    avatar_refresh_seconds: int = 86400


class DeckChannelMappingItem(BaseModel):
    board_id: int
    channel: str


class DeckReminderConfig(BaseModel):
    enabled: bool = True
    cards_processed_storage: str = "/data/processed_cards.json"
    notify_before_days: int = 3
    remind_after_days: int = 3
    deck_channel_mapping: List[DeckChannelMappingItem] = Field(default_factory=list)


class CalendarNotifierConfig(BaseModel):
    caldav_url: Optional[str] = None
    enabled: bool = True
    search_start_days: int = 0
    search_end_days: int = 8
    timezone: str = "Europe/Vienna"
    channel_keywords: Dict[str, List[str]] = Field(default_factory=dict)


class MailerListItem(BaseModel):
    prefix: str = ""
    groups: List[str] = Field(default_factory=list)


class MailerConfig(BaseModel):
    restrict_sender: bool = False
    reply_to_original_sender: bool = True
    send_to_sender: bool = False
    lists: Dict[str, MailerListItem] = Field(default_factory=dict)


class BotConfig(BaseModel):
    sleep_minutes: int = 30
    organisation: OrganisationConfig = OrganisationConfig()
    avatare: AvatarConfig = AvatarConfig()
    deck_reminder: DeckReminderConfig = DeckReminderConfig()
    calendar_notifier: CalendarNotifierConfig = CalendarNotifierConfig()
    mailer: MailerConfig = MailerConfig()

    data: Dict = {}

    @field_validator("sleep_minutes")
    def sleep_positive(cls, v):
        if v < 0:
            raise ValueError("sleep_minutes must be non-negative")
        return v

    @classmethod
    def load_config(cls) -> BotConfig:
        """Load bot configuration from the Nextcloud Collectives configuration page."""
        config_page = CollectivePage.get_from_page_id(
            page_id=settings.nextcloud.configuration_page_id
        )

        raw = config_page.content or ""
        yaml_text = extract_yaml_block(raw)
        if not yaml_text:
            raise ValueError(
                "No YAML configuration block found in the configuration page"
            )

        parsed = yaml.safe_load(yaml_text)
        logger.info("Loaded bot configuration from collectives page %s", config_page.id)

        return cls(**parsed)


def extract_yaml_block(content: str) -> Optional[str]:
    """Extract the first fenced YAML block (``` ... ```) using a regular expression.

    This will match an optional language marker after the opening fence, e.g. ```yaml\n...
    Returns the inner YAML text or None if not found.
    """
    if not content:
        return None

    # Regex: opening fence ```, optional language marker until newline, then capture until closing fence
    m = re.search(r"```(?:[^\n]*\n)?(.*?)```", content, flags=re.DOTALL)
    if not m:
        return None

    yaml_text = m.group(1).strip()
    # replace "\t" with spaces for YAML parsing
    yaml_text = yaml_text.replace("\t", "  ")

    return yaml_text if yaml_text else None


class LazyBotConfig:
    """Lazy loader for BotConfig.

    - Loads on first access.
    - On every access it will attempt to reload from DB with retries.
    - If reload fails, returns last cached value (if any) or raises.
    """

    def __init__(self, retries: int = 3, delay: float = 1.0):
        self._lock = threading.RLock()
        self._cached: Optional[BotConfig] = None
        self._retries = int(retries)
        self._delay = float(delay)

    def reload(self) -> BotConfig:
        last_exc: Exception | None = None
        for attempt in range(1, self._retries + 1):
            try:
                cfg = BotConfig.load_config()
                with self._lock:
                    self._cached = cfg
                logger.debug(
                    "Loaded bot configuration (attempt %d/%d)", attempt, self._retries
                )
                return cfg
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Failed to load bot configuration (attempt %d/%d): %s",
                    attempt,
                    self._retries,
                    exc,
                )
                time.sleep(self._delay)
        # all attempts failed
        with self._lock:
            if self._cached is not None:
                logger.warning(
                    "Returning cached bot configuration after %d failed reload attempts",
                    self._retries,
                )
                return self._cached
        # no cached config available -> re-raise last exception
        raise last_exc or RuntimeError("Unknown error loading BotConfig")

    def get(self) -> BotConfig:
        # ensure at least one load happened and attempt to refresh on each access
        with self._lock:
            if self._cached is None:
                return self.reload()

        return self._cached
        # try to reload but don't blow up if reload fails; return cached instead
        # try:
        #     return self.reload()
        # except Exception:
        #     with self._lock:
        #         return self._cached  # type: ignore[return-value]

    def __getattr__(self, name: str):
        cfg = self.get()
        return getattr(cfg, name)

    def __repr__(self) -> str:
        with self._lock:
            return f"<LazyBotConfig cached={'yes' if self._cached else 'no'}>"


# export a module-level instance
bot_config = LazyBotConfig()
