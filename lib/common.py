import gettext
import locale
from gettext import gettext as _  # noqa: F401
from typing import Optional

import sentry_sdk
from pydantic import BaseModel, HttpUrl, validator
from pydantic_settings import BaseSettings, SettingsConfigDict

available_languages = {"de": "Deutsch", "en": "English"}


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

    @validator("authentik_base_url", always=True)
    def set_authentik_base_url(cls, v, values):
        return v or values.get("provider_base_url")

    @validator("authorization_endpoint", always=True)
    def set_authorization_endpoint(cls, v, values):
        return (
            v or str(values.get("provider_base_url", "")) + "application/o/authorize/"
        )

    @validator("token_endpoint", always=True)
    def set_token_endpoint(cls, v, values) -> str:
        return v or str(values.get("provider_base_url", "")) + "application/o/token/"

    @validator("userinfo_endpoint", always=True)
    def set_userinfo_endpoint(cls, v, values) -> str:
        return v or str(values.get("provider_base_url", "")) + "application/o/userinfo/"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__")

    sentry_dsn: str = ""

    timezone: str = "Europe/Berlin"
    name: str = "Nextcloud-Bot"
    default_language: str = "de"

    auth: AuthSettings = AuthSettings()


settings = Settings()

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        enable_tracing=False,
    )
