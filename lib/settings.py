import gettext
import locale
import logging
import re
from typing import Optional

import sentry_sdk
from pydantic import BaseModel, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Configure logging to suppress verbose HTTP logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("chromadb").setLevel(logging.WARNING)


# Translation functions that will be updated by set_language()
def _(message: str) -> str:
    """Translate a message using the current language setting."""
    return _gettext(message)


def _n(singular: str, plural: str, n: int) -> str:
    """Translate a message with plural forms using the current language setting."""
    return _ngettext(singular, plural, n)


# Internal references that will be updated
_gettext = gettext.gettext
_ngettext = gettext.ngettext


available_languages = {"de": "Deutsch", "en": "English"}


user_regex = re.compile(r"mention://user/([A-Za-z0-9_.-]+)")


def set_language(language: str):
    global _gettext
    global _ngettext

    if language and language == "de":
        localizator = gettext.translation(
            "messages", localedir="locales", languages=[language]
        )
        localizator.install()
        _gettext = localizator.gettext
        _ngettext = localizator.ngettext
        locale_str = "de_AT.UTF-8"
    else:
        _gettext = gettext.gettext
        _ngettext = gettext.ngettext
        locale_str = "en_US.UTF-8"

    locale.setlocale(locale.LC_ALL, locale_str)


class AuthSettings(BaseModel):
    provider_base_url: Optional[HttpUrl] = None
    authorization_endpoint: str = ""
    token_endpoint: str = ""
    userinfo_endpoint: str = ""
    scope: str = "openid email profile"

    client_id: str = ""
    client_secret: str = ""

    # Required to retrieve users from authentik
    authentik_base_url: Optional[HttpUrl] = None
    authentik_token: str = ""

    board_group_name: str = "Vorstand"

    @field_validator("authentik_base_url")
    def set_authentik_base_url(cls, v, values):
        return v or values.get("provider_base_url")

    @field_validator("authorization_endpoint")
    def set_authorization_endpoint(cls, v, values):
        return (
            v or str(values.get("provider_base_url", "")) + "application/o/authorize/"
        )

    @field_validator("token_endpoint")
    def set_token_endpoint(cls, v, values) -> str:
        return v or str(values.get("provider_base_url", "")) + "application/o/token/"

    @field_validator("userinfo_endpoint")
    def set_userinfo_endpoint(cls, v, values) -> str:
        return v or str(values.get("provider_base_url", "")) + "application/o/userinfo/"


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


class CouchDBSettings(BaseModel):
    url: HttpUrl = HttpUrl("http://admin:password@localhost:5984/")
    database_name: str = "nextcloud_bot"


class ChromaDBSettings(BaseModel):
    host: str = "localhost"
    port: int = 8800

    # huggingface embedding server url, use http://localhost:8001/embed for local server
    hf_embedding_server_url: str = ""
    gemini_api_key: str = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__")

    sentry_dsn: str = ""
    sentry_sample_rate: float = 1.0
    sentry_logs: bool = True

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    timezone: str = "Europe/Berlin"
    locale: str = "de_AT.UTF-8"

    name: str = "Nextcloud-Bot"
    default_language: str = "de"

    log_level: str = "INFO"

    imaginary_url: str = "http://localhost:9001"

    couchdb: CouchDBSettings = CouchDBSettings()
    chromadb: ChromaDBSettings = ChromaDBSettings()

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
