"""Embedded media (attachments) of protocol pages, stored in the database.

Collectives stores page attachments in a ``.attachments.<file-id>/`` folder
next to the markdown file. To keep protocol history self-contained, referenced
attachments are copied into this table and served by the app itself.
"""

import logging
from typing import Optional, Set

import edgy

from app.models.base import BaseDBModel

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
    data: bytes = edgy.BinaryField()

    natural_key_fields = ("page_id", "name")

    class Meta:
        tablename = "protocol_media"

    def __str__(self) -> str:
        return f"ProtocolMedia(page_id={self.page_id}, name={self.name})"

    @classmethod
    def get_for_page(cls, page_id: int, name: str) -> Optional["ProtocolMedia"]:
        return cls.fetch_one(page_id=page_id, name=name)

    @classmethod
    def names_for_page(cls, page_id: int) -> Set[str]:
        """Names of all stored attachments of a page.

        Uses a name-only query so the (potentially large) data blobs are
        not loaded just to check for existence.
        """
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
