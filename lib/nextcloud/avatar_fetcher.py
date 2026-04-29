import base64
import logging
import shutil
from functools import cached_property
from pathlib import Path
from time import time
from typing import List, NamedTuple, Optional

import requests

from lib.settings import settings

from .config import AvatarConfig
from .models.user import NCUserList

logger = logging.getLogger()


class AvatarResult(NamedTuple):
    content: bytes
    # Additional base names (without extension) the avatar should also be saved as
    extra_names: List[str]


class AvatarFetcher:
    NEXTCLOUD_AVATAR_URL = "/index.php/avatar/{username}/200?v=1'"
    AUTHENTIK_USERS_URL = "/api/v3/core/users/"

    config: AvatarConfig

    def __init__(self, config: AvatarConfig) -> None:
        self.config = config

    @cached_property
    def base_folder(self) -> Path:
        base_folder = Path(self.config.avatar_folder)
        if not base_folder.exists():
            base_folder.mkdir()

        return base_folder

    def fetch_images(self, nc_users: NCUserList):
        logger.debug("Fetching missing avatar images")
        for user in nc_users.get_enabled_users():
            self.fetch_avatar(user.username)

    def fetch_avatar(self, username: str):
        # username == nextcloud username == authentik uid
        avatar_path_tmp = self.base_folder / f"{username}"
        avatar_path_jpg = self.base_folder / f"{username}.jpg"
        avatar_path_dot_jpg = self.base_folder / f"{username.replace('_', '.')}.jpg"

        # skip if avatar_path_jpg already exists and is younger than configured refresh interval
        if avatar_path_jpg.exists() and (
            avatar_path_jpg.stat().st_mtime
            > (time() - self.config.avatar_refresh_seconds or 86400)
        ):
            return

        result = self._fetch_raw_avatar(username)
        if result is None:
            logger.warning(
                "No avatar found for user %s in any configured source", username
            )
            return

        logger.debug(avatar_path_tmp)
        with avatar_path_tmp.open("wb") as avatar_file:
            avatar_file.write(result.content)

        try:
            with avatar_path_tmp.open("rb") as f:
                files = {"file": (avatar_path_jpg.name, f, "image/jpeg")}
                response = requests.post(
                    f"{settings.imaginary_url}/convert",
                    params={"type": "jpeg"},
                    files=files,
                )
                response.raise_for_status()

            with avatar_path_jpg.open("wb") as out_file:
                out_file.write(response.content)

            # copy to dot_jpg path only if it's different from the primary path
            if avatar_path_jpg != avatar_path_dot_jpg:
                shutil.copy(avatar_path_jpg, avatar_path_dot_jpg)

            # copy to any extra filenames returned by the source (e.g. authentik username)
            for extra_name in result.extra_names:
                extra_path = self.base_folder / f"{extra_name}.jpg"
                if extra_path != avatar_path_jpg:
                    logger.debug(
                        "Copying avatar for %s to extra path %s", username, extra_path
                    )
                    shutil.copy(avatar_path_jpg, extra_path)

            avatar_path_tmp.unlink()
        except Exception as e:
            logger.error("Error converting avatar image %s: %s", avatar_path_tmp, e)

    def _fetch_raw_avatar(self, username: str) -> Optional[AvatarResult]:
        """Try each configured avatar source in order and return the first successful result."""
        for source in self.config.avatar_sources:
            result = self._fetch_from_source(source, username)
            if result is not None:
                logger.debug("Fetched avatar for %s from source '%s'", username, source)
                return result
        return None

    def _fetch_from_source(self, source: str, username: str) -> Optional[AvatarResult]:
        """Dispatch avatar fetching to the appropriate provider method."""
        if source == "nextcloud":
            return self._fetch_from_nextcloud(username)
        elif source == "authentik":
            return self._fetch_from_authentik(username)
        else:
            logger.warning("Unknown avatar source '%s', skipping", source)
            return None

    def _fetch_from_nextcloud(self, username: str) -> Optional[AvatarResult]:
        """Fetch avatar bytes from Nextcloud. Returns None if unavailable."""
        if not settings.nextcloud.base_url:
            logger.debug(
                "Nextcloud base_url not configured, skipping nextcloud avatar source"
            )
            return None

        try:
            response = requests.get(
                f"{settings.nextcloud.base_url}{self.NEXTCLOUD_AVATAR_URL.format(username=username)}",
                auth=(
                    settings.nextcloud.admin_username,
                    settings.nextcloud.admin_password,
                ),
                headers={"OCS-APIRequest": "true"},
            )
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                logger.debug(
                    "Nextcloud returned non-image content-type '%s' for user %s, skipping",
                    content_type,
                    username,
                )
                return None

            return (
                AvatarResult(content=response.content, extra_names=[])
                if response.content
                else None
            )
        except Exception as e:
            logger.warning(
                "Failed to fetch avatar from Nextcloud for user %s: %s", username, e
            )
            return None

    def _fetch_from_authentik(self, username: str) -> Optional[AvatarResult]:
        """Fetch avatar bytes from authentik by looking up the user via their uid field
        (which maps to the Nextcloud username). Returns None if unavailable.

        The converted avatar is saved under the uid-based filename (primary) and also
        under the authentik username field as an additional copy.
        """
        if not settings.auth.authentik_base_url:
            logger.debug(
                "authentik_base_url not configured, skipping authentik avatar source"
            )
            return None

        if not settings.auth.authentik_token:
            logger.debug(
                "authentik_token not configured, skipping authentik avatar source"
            )
            return None

        try:
            headers = {
                "Authorization": f"Bearer {settings.auth.authentik_token}",
                "Accept": "application/json",
            }

            # The Nextcloud username equals the authentik uid field
            response = requests.get(
                f"{settings.auth.authentik_base_url}{self.AUTHENTIK_USERS_URL}",
                headers=headers,
                params={"uuid": username},
            )
            response.raise_for_status()

            data = response.json()
            results = data.get("results", [])
            if not results:
                logger.debug("No authentik user found for uid %s", username)
                return None

            user = results[0]

            avatar_url = user.get("avatar")
            if not avatar_url:
                logger.debug(
                    "No avatar URL in authentik user object for uid %s", username
                )
                return None

            if avatar_url.startswith("data:image"):
                # Inline base64 data URL: data:image/<type>;base64,<data>
                try:
                    header, encoded = avatar_url.split(",", 1)
                    image_content = base64.b64decode(encoded)
                except Exception as e:
                    logger.debug(
                        "Failed to decode data:image URL for uid %s: %s", username, e
                    )
                    return None

                if not image_content:
                    return None
            elif avatar_url.startswith("http"):
                avatar_response = requests.get(avatar_url, headers=headers)
                avatar_response.raise_for_status()

                content_type = avatar_response.headers.get("Content-Type", "")
                if not content_type.startswith("image/"):
                    logger.debug(
                        "authentik returned non-image content-type '%s' for uid %s, skipping",
                        content_type,
                        username,
                    )
                    return None

                if not avatar_response.content:
                    return None

                image_content = avatar_response.content
            else:
                logger.debug(
                    "Unrecognised avatar URL scheme for uid %s, skipping", username
                )
                return None

            # Collect extra filenames: save under the authentik username as well,
            # if it differs from the uid (= Nextcloud username)
            extra_names: List[str] = []
            authentik_username = user.get("username")
            if authentik_username and authentik_username != username:
                extra_names.append(authentik_username)

            return AvatarResult(content=image_content, extra_names=extra_names)
        except Exception as e:
            logger.warning(
                "Failed to fetch avatar from authentik for uid %s: %s", username, e
            )
            return None
