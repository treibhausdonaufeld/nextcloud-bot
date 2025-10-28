import logging
import shutil
import subprocess
from functools import cached_property
from pathlib import Path
from time import time
from typing import Dict

import requests

from .config import BotConfig
from .models.user import NCUserList

logger = logging.getLogger()


class AvatarFetcher:
    AVATAR_URL = "/index.php/avatar/{username}/200?v=1'"

    @cached_property
    def config(self) -> Dict:
        return BotConfig.data["nextcloud"]

    @cached_property
    def base_folder(self) -> Path:
        base_folder = Path(self.config["avatar_folder"])
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
        avatar_path_dot_jpg = self.base_folder / f"{username.replace('_', '.')}.jpg"

        # skip if avatar_path_jpg already exists and is younger than 24 hours
        if avatar_path_jpg.exists() and (
            avatar_path_jpg.stat().st_mtime
            > (time() - self.config.get("avatar_refresh_seconds", 86400))
        ):
            return

        response = requests.get(
            f"{self.config['host']}{self.AVATAR_URL.format(username=username)}",
            auth=(self.config["username"], self.config["password"]),
            headers={"OCS-APIRequest": "true"},
        )

        # ext = {"image/jpeg": "jpg", "image/png": "png"}.get(
        #     response.headers["Content-Type"]
        # )
        logger.debug(avatar_path_tmp)
        with avatar_path_tmp.open("wb") as avatar_file:
            avatar_file.write(response.content)

        try:
            subprocess.check_call(
                ["magick", str(avatar_path_tmp), str(avatar_path_jpg)]
            )

            # copy avatar_path_tmp to avatar_path_dot_jpg
            shutil.copy(avatar_path_jpg, avatar_path_dot_jpg)

            avatar_path_tmp.unlink()
        except subprocess.CalledProcessError as e:
            logger.error("Error converting avatar image %s: %s", avatar_path_tmp, e)
