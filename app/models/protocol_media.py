"""Embedded media (attachments) of protocol pages, stored on disk.

Collectives stores page attachments in a ``.attachments.<file-id>/`` folder
next to the markdown file. Referenced attachments are copied to the local
media folder (``settings.media_folder``, env var MEDIA_FOLDER) and served by
the app itself; this table holds the metadata and the file location.

Files are laid out as ``YYYY/MM/DD/<page-id>/attachments/<folder-id>/<name>``
(date = the protocol's original date), so old attachments can easily be
pruned by date when disk space runs low. A pruned file simply 404s in the
viewer; its metadata row keeps the sync from re-downloading it.
"""

import logging
from pathlib import Path
from typing import Optional, Set

import edgy

from app.models.base import BaseDBModel
from app.settings import settings

logger = logging.getLogger(__name__)


class ProtocolMedia(BaseDBModel):
    page_id: int = edgy.BigIntegerField(index=True)
    # "<attachment-folder-id>/<filename>" as referenced in the markdown
    # (URL-decoded); the folder id disambiguates same-named files
    name: str = edgy.CharField(max_length=512)
    # original relative path in the markdown, e.g. ".attachments.123/img.png"
    path: str = edgy.CharField(max_length=1024, default="")
    content_type: str = edgy.CharField(
        max_length=128, default="application/octet-stream"
    )
    size: int = edgy.BigIntegerField(default=0)
    # storage location relative to settings.media_folder
    file_path: str = edgy.CharField(max_length=1024, default="")

    natural_key_fields = ("page_id", "name")

    class Meta:
        tablename = "protocol_media"

    def __str__(self) -> str:
        return f"ProtocolMedia(page_id={self.page_id}, name={self.name})"

    @property
    def absolute_path(self) -> Path:
        return Path(settings.media_folder) / self.file_path

    def write_file(self, data: bytes) -> None:
        """Write the attachment bytes to disk (creating parent folders)."""
        target = self.absolute_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.size = len(data)

    def read_file(self) -> bytes | None:
        """Read the attachment from disk; None when the file is gone
        (e.g. manually pruned to free space)."""
        try:
            return self.absolute_path.read_bytes()
        except OSError:
            return None

    def before_remove(self) -> None:
        """Delete the file and clean up empty date/page folders."""
        if not self.file_path:
            return
        path = self.absolute_path
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Could not delete media file %s: %s", path, e)
            return

        # best-effort: remove now-empty parent folders up to the media root
        root = Path(settings.media_folder).resolve()
        parent = path.parent.resolve()
        while parent != root and root in parent.parents:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    @classmethod
    def get_for_page(cls, page_id: int, name: str) -> Optional["ProtocolMedia"]:
        return cls.fetch_one(page_id=page_id, name=name)

    @classmethod
    def names_for_page(cls, page_id: int) -> Set[str]:
        """Names of all stored attachments of a page (name-only query)."""
        from app.db import fetch_all_sql

        rows = fetch_all_sql(
            "SELECT name FROM protocol_media WHERE page_id = :page_id",
            {"page_id": page_id},
        )
        return {row["name"] for row in rows}

    @classmethod
    def remove_for_page(cls, page_id: int) -> None:
        for media in cls.fetch(page_id=page_id, limit=10000):
            media.remove()
