import gettext
import locale
import logging
import re
from gettext import gettext as _  # noqa: F401
from typing import Optional

import sentry_sdk
from pydantic import BaseModel, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Ensure _n is always defined for mypy
_n = gettext.ngettext


available_languages = {"de": "Deutsch", "en": "English"}


user_regex = re.compile(r"mention://user/([A-Za-z0-9_.-]+)")


def set_language(language: str):
    global _
    global _n

    if language and language == "de":
        localizator = gettext.translation(
            "messages", localedir="locales", languages=[language]
        )
        localizator.install()
        _ = localizator.gettext
        _n = localizator.ngettext
        locale_str = "de_AT.UTF-8"
    else:
        _ = gettext.gettext
        _n = gettext.ngettext
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


class MailSettings(BaseModel):
    smtp_server: str = ""
    smtp_port: int = 25
    use_tls: bool = True
    username: Optional[str] = None
    password: Optional[str] = None
    from_address: str = ""

    imap_server: str = ""
    imap_port: int = 143
    use_imap_tls: bool = True
    imap_username: Optional[str] = None
    imap_password: Optional[str] = None


class NextcloudSettings(BaseModel):
    base_url: Optional[HttpUrl] = None
    admin_username: str = ""
    admin_password: str = ""

    collectives_id: int = 1
    configuration_page_id: int = 15158708


class CouchDBSettings(BaseModel):
    url: HttpUrl = HttpUrl("http://admin:password@localhost:5984/")
    database_name: str = "nextcloud_bot"


class ChromaDBSettings(BaseModel):
    host: str = "localhost"
    port: int = 8800

    hf_embedding_server_url: str = "http://localhost:8001/embed"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__")

    sentry_dsn: str = ""

    timezone: str = "Europe/Berlin"
    locale: str = "de_AT.UTF-8"

    name: str = "Nextcloud-Bot"
    default_language: str = "de"

    log_level: str = "INFO"

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
        enable_tracing=False,
    )

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
