from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from lib.nextcloud.models import CollectivePage
from lib.settings import settings

logger = logging.getLogger(__name__)


class OrganisationConfig(BaseModel):
    group_prefixes: List[str] = Field(default_factory=lambda: ["AG", "UG", "PG"])
    extra_groups: List[str] = Field(default_factory=list)


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
    def load_config(cls) -> BotConfig | None:
        config_page = CollectivePage(
            id=CollectivePage.build_id(settings.nextcloud.configuration_page_id)
        )
        config_page.load()

        raw = config_page.content or ""
        yaml_text = extract_yaml_block(raw)
        if not yaml_text:
            raise ValueError(
                "No YAML configuration block found in the configuration page"
            )

        parsed = yaml.safe_load(yaml_text)
        logger.info("Loaded bot configuration from collectives page %s", config_page.id)

        return cls.model_validate(parsed)


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


bot_config = BotConfig.load_config()
