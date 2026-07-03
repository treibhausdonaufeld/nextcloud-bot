import gettext
import locale
import logging
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Optional

import sentry_sdk
from pydantic import BaseModel, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Configure logging to suppress verbose HTTP logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

LOCALES_DIR = Path(__file__).parent.parent / "locales"

available_languages = {"de": "Deutsch", "en": "English"}

user_regex = re.compile(r"mention://user/([A-Za-z0-9_.-]+)")


# The active translation catalog is stored in a ContextVar so each request /
# worker thread can have its own language without racing on module globals.
_null_translation = gettext.NullTranslations()
_translations: dict[str, gettext.NullTranslations] = {}
_current_translation: ContextVar[gettext.NullTranslations] = ContextVar(
    "current_translation", default=_null_translation
)


def _(message: str) -> str:
    """Translate a message using the current language setting."""
    return _current_translation.get().gettext(message)


def _n(singular: str, plural: str, n: int) -> str:
    """Translate a message with plural forms using the current language setting."""
    return _current_translation.get().ngettext(singular, plural, n)


def set_language(language: str) -> None:
    """Activate the gettext catalog for `language` in the current context."""
    if language and language in available_languages and language != "en":
        if language not in _translations:
            _translations[language] = gettext.translation(
                "messages",
                localedir=str(LOCALES_DIR),
                languages=[language],
                fallback=True,
            )
        _current_translation.set(_translations[language])
    else:
        _current_translation.set(_null_translation)


def setup_locale() -> None:
    """Set the process-wide locale once at startup (used for date formatting).

    locale.setlocale is process-global and not thread-safe, so unlike the
    gettext catalog it is NOT switched per request.
    """
    locale_str = "de_AT.UTF-8" if settings.default_language == "de" else "en_US.UTF-8"
    try:
        locale.setlocale(locale.LC_ALL, locale_str)
    except locale.Error:
        logging.getLogger(__name__).warning("Locale %s not available", locale_str)


class AuthSettings(BaseModel):
    provider_base_url: Optional[HttpUrl] = None

    # Required to retrieve user avatars from authentik
    authentik_base_url: Optional[HttpUrl] = None
    authentik_token: str = ""

    board_group_name: str = "Vorstand"

    @field_validator("authentik_base_url")
    def set_authentik_base_url(cls, v, values):
        return v or values.get("provider_base_url")


class RocketchatSettings(BaseModel):
    hook_url: Optional[HttpUrl] = None

    info_channel: str = ""
    error_channel: str = ""

    # user to overwrite all notifications to this user/channel
    channel_overwrite: str = ""


class MailSettings(BaseModel):
    smtp_server: str = ""
    smtp_port: int = 25
    smtp_use_tls: bool = True
    smtp_username: str = ""
    smtp_password: str = ""
    from_address: str = ""
    send_delay_seconds: int = 1

    imap_server: str = ""
    imap_port: int = 143
    imap_use_tls: bool = True
    imap_username: str = ""
    imap_password: str = ""


class NextcloudSettings(BaseModel):
    base_url: Optional[HttpUrl] = None
    admin_username: str = ""
    admin_password: str = ""

    collectives_id: int = 1
    configuration_page_id: int = 15158708
    configuration_page_name: str = "Bot-Config"

    timeline_page_name: str = "Timeline"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__")

    sentry_dsn: str = ""
    sentry_sample_rate: float = 1.0
    sentry_logs: bool = True

    timezone: str = "Europe/Berlin"
    locale: str = "de_AT.UTF-8"

    name: str = "Nextcloud-Bot"
    default_language: str = "de"

    log_level: str = "INFO"

    # SQLite database location (async driver required by Edgy/databasez)
    database_url: str = "sqlite+aiosqlite:///data/nextcloud_bot.db"

    auth: AuthSettings = AuthSettings()
    nextcloud: NextcloudSettings = NextcloudSettings()
    rocketchat: RocketchatSettings = RocketchatSettings()
    mailinglist: MailSettings = MailSettings()


settings = Settings()

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        send_default_pii=True,
        # Enable sending logs to Sentry
        enable_logs=settings.sentry_logs,
        # Set traces_sample_rate to 1.0 to capture 100%
        # of transactions for tracing.
        traces_sample_rate=settings.sentry_sample_rate,
    )

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
