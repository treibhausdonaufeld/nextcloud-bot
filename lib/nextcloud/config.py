from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from lib.nextcloud.models import CollectivePage
from lib.settings import settings


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

    @field_validator("sleep_minutes")
    def sleep_positive(cls, v):
        if v < 0:
            raise ValueError("sleep_minutes must be non-negative")
        return v

    @classmethod
    def load_config(cls, config_file: Path):
        config_page = CollectivePage(
            id=CollectivePage.build_id(settings.nextcloud.configuration_page_id)
        )
        config_page.load_from_db()

        data = yaml.safe_load(config_page.content or "")
        cls.data = data
