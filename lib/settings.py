from pathlib import Path
from typing import Any, Dict, Optional

import sentry_sdk
from pydantic import BaseModel, HttpUrl, model_validator, validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseModel):
    data_dir_path: Path

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

    @property
    def members_file(self) -> Path:
        return self.data_dir_path / "members.json"

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

    @model_validator(mode="before")
    @classmethod
    def build_extra(cls, data) -> Dict[str, Any]:
        """Add data dir path to all configured sub-settings"""
        data_dir_path = data.get("data_dir_path") or Path("data")

        for key in ["signal", "reminder", "auth", "nuki", "money", "power"]:
            data.setdefault(key, {})
            data[key]["data_dir_path"] = data_dir_path

        return data


settings = Settings()

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        enable_tracing=False,
    )
