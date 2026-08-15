import gettext
import json
import locale
import logging
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, List, Optional

import sentry_sdk
from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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

    # Only users belonging to this authentik group count as members of the
    # association and are offered in the member overview and user pickers.
    # Set AUTH__MEMBER_GROUP_NAME to an empty string to show every user.
    member_group_name: str = "Mitglieder"

    @model_validator(mode="after")
    def set_authentik_base_url(self) -> "AuthSettings":
        # Field validators do not run for unset defaults, so the fallback has
        # to happen after the model is built — otherwise deployments that only
        # set AUTH__PROVIDER_BASE_URL end up without an authentik connection.
        if self.authentik_base_url is None:
            self.authentik_base_url = self.provider_base_url
        return self


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


class MatrixSettings(BaseModel):
    """Connection to a Matrix homeserver for the group chat rooms.

    The whole feature is opt-in: without a homeserver URL *and* an admin
    access token (a normal user token with the right to create rooms works
    too) nothing is created and no request is sent — see `enabled`.
    """

    # e.g. https://matrix.example.com
    homeserver_url: Optional[HttpUrl] = None

    # Access token used for every request (Authorization: Bearer ...).
    admin_token: str = ""

    # Server part of room aliases (#ag-struktur:example.com). This is the
    # homeserver's `server_name`, which is often shorter than the hostname of
    # the client API (matrix.example.com vs example.com) — hence the explicit
    # setting, falling back to the URL's host.
    server_name: str = ""

    # Server part of the user ids that get invited. Defaults to server_name;
    # set it when the association's accounts live on another server.
    user_domain: str = ""

    # Optional prefix for every generated room alias, e.g. "thd-" turns
    # "AG Struktur" into #thd-ag-struktur:example.com.
    room_prefix: str = ""

    # Rooms every member belongs to, independent of any group, as a
    # comma-separated list: MATRIX__DEFAULT_ROOMS="Allgemein, Ankündigungen"
    # creates #allgemein and #ankuendigungen and invites all members. A JSON
    # list works as well. `NoDecode` keeps pydantic-settings from insisting
    # on the JSON form.
    default_rooms: Annotated[List[str], NoDecode] = Field(default_factory=list)

    @field_validator("default_rooms", mode="before")
    @classmethod
    def split_default_rooms(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):
            return json.loads(text)
        return [name.strip() for name in text.split(",") if name.strip()]

    @model_validator(mode="after")
    def set_domains(self) -> "MatrixSettings":
        if not self.server_name and self.homeserver_url:
            self.server_name = self.homeserver_url.host or ""
        if not self.user_domain:
            self.user_domain = self.server_name
        return self

    @property
    def enabled(self) -> bool:
        """Whether the group chat room sync should run at all."""
        return bool(self.homeserver_url and self.admin_token and self.server_name)


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

    # Optional override for the avatar storage folder. When set (env var
    # AVATAR_FOLDER) this takes precedence over the bot-config page's
    # avatare.avatar_folder value, since the path is an infrastructure
    # concern (it must match the container's volume mount).
    avatar_folder: Optional[str] = None

    # While both a Matrix homeserver and a Rocket.Chat webhook are
    # configured, send every notification to both instead of using
    # Rocket.Chat only as a fallback — so a migration can run with the old
    # chat still live. Set NOTIFY_DUAL_SEND=false to go back to
    # "Matrix first, Rocket.Chat only when Matrix cannot deliver".
    notify_dual_send: bool = True

    # Redirect *every* notification to this channel or user, whichever
    # backend it would go out through (env var NOTIFY_CHANNEL_OVERWRITE).
    # For testing a deployment without messaging the whole association:
    # "@max.mueller" sends everything as a direct message to that user,
    # "bot-test" sends everything to that one channel. Takes precedence over
    # the bot-config page's notifier.channel_overwrite.
    notify_channel_overwrite: str = ""

    # Folder for protocol attachments (env var MEDIA_FOLDER). Files are
    # stored as YYYY/MM/DD/<page-id>/attachments/<folder-id>/<name> so old
    # attachments can easily be pruned by date when space runs low.
    media_folder: str = "/data/media"

    auth: AuthSettings = AuthSettings()
    nextcloud: NextcloudSettings = NextcloudSettings()
    matrix: MatrixSettings = MatrixSettings()
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
