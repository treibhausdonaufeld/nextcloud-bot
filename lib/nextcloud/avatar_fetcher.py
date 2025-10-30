import logging
from functools import cached_property
from pathlib import Path
from time import time

import requests

from lib.settings import settings

from .config import AvatarConfig
from .models.user import NCUserList

logger = logging.getLogger()


class AvatarFetcher:
    AVATAR_URL = "/index.php/avatar/{username}/200?v=1'"
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
        for username in nc_users.get_all_usernames():
            self.fetch_avatar(username)

    def fetch_avatar(self, username: str):
        avatar_path_tmp = self.base_folder / f"{username}"
        avatar_path_jpg = self.base_folder / f"{username}.jpg"

        # skip if avatar_path_jpg already exists and is younger than 24 hours
        if avatar_path_jpg.exists() and (
            avatar_path_jpg.stat().st_mtime
            > (time() - self.config.avatar_refresh_seconds or 86400)
        ):
            return

        response = requests.get(
            f"{settings.nextcloud.base_url}{self.AVATAR_URL.format(username=username)}",
            auth=(settings.nextcloud.admin_username, settings.nextcloud.admin_password),
            headers={"OCS-APIRequest": "true"},
        )

        # ext = {"image/jpeg": "jpg", "image/png": "png"}.get(
        #     response.headers["Content-Type"]
        # )
        logger.debug(avatar_path_tmp)
        with avatar_path_tmp.open("wb") as avatar_file:
            avatar_file.write(response.content)

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

            avatar_path_tmp.unlink()
        except Exception as e:
            logger.error("Error converting avatar image %s: %s", avatar_path_tmp, e)
