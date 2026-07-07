"""Version history for protocol pages.

Every time a protocol page's markdown content changes during a sync, a full
snapshot is stored together with a unified diff to the previous version and
the Nextcloud user who made the change. This keeps the protocol history
self-contained in the bot's own database, independent of Nextcloud.
"""

import difflib
import logging
from typing import TYPE_CHECKING, List, Optional

import edgy

from app.models.base import BaseDBModel, format_timestamp

if TYPE_CHECKING:
    from app.models.collective_page import CollectivePage

logger = logging.getLogger(__name__)


class ProtocolVersion(BaseDBModel):
    page_id: int = edgy.BigIntegerField(index=True)
    # per-page version counter, starting at 1
    version: int = edgy.IntegerField(default=1)

    title: str = edgy.CharField(max_length=512, default="")
    # full markdown snapshot of the page at this version
    content: str = edgy.TextField(default="")
    # unified diff against the previous version ("" for the first version)
    diff: str = edgy.TextField(default="")
    # Nextcloud user id of the person who made the change
    editor: str | None = edgy.CharField(max_length=255, null=True)
    # Nextcloud modification timestamp of the page at snapshot time
    page_timestamp: int | None = edgy.BigIntegerField(null=True)
    # set when this version was created by restoring an older version
    restored_from: int | None = edgy.IntegerField(null=True)

    natural_key_fields = ("page_id", "version")

    class Meta:
        tablename = "protocol_versions"

    def __str__(self) -> str:
        return f"ProtocolVersion(page_id={self.page_id}, version={self.version})"

    @property
    def formatted_timestamp(self) -> str | None:
        return format_timestamp(self.page_timestamp) or format_timestamp(
            self.updated_at
        )

    @classmethod
    def latest_for_page(cls, page_id: int) -> Optional["ProtocolVersion"]:
        versions = cls.fetch(page_id=page_id, order_by="-version", limit=1)
        return versions[0] if versions else None

    @classmethod
    def history_for_page(cls, page_id: int) -> List["ProtocolVersion"]:
        """All versions of a page, newest first."""
        return cls.fetch(page_id=page_id, order_by="-version", limit=10000)

    @staticmethod
    def compute_diff(old: str, new: str, old_label: str, new_label: str) -> str:
        """Unified diff between two markdown snapshots."""
        lines = difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile=old_label,
            tofile=new_label,
            lineterm="",
        )
        return "\n".join(lines)

    @classmethod
    def record(
        cls,
        page: "CollectivePage",
        editor: str | None = None,
        restored_from: int | None = None,
    ) -> Optional["ProtocolVersion"]:
        """Snapshot the page content as a new version if it changed.

        Idempotent: when the content equals the latest stored version, no new
        version is created. Returns the created version or None.
        """
        content = page.content or ""
        latest = cls.latest_for_page(page.page_id)
        if latest is not None and (latest.content or "") == content:
            return None

        version_no = (latest.version + 1) if latest is not None else 1
        snapshot = cls(
            page_id=page.page_id,
            version=version_no,
            title=page.title or "",
            content=content,
            diff=cls.compute_diff(
                latest.content or "", content, f"v{latest.version}", f"v{version_no}"
            )
            if latest is not None
            else "",
            editor=editor if editor is not None else page.last_user_id,
            page_timestamp=page.timestamp,
            restored_from=restored_from,
        )
        snapshot.store()
        logger.info(
            "Recorded protocol version %d for page %s (editor=%s)",
            version_no,
            page.page_id,
            snapshot.editor,
        )
        return snapshot

    @classmethod
    def remove_for_page(cls, page_id: int) -> None:
        for version in cls.fetch(page_id=page_id, limit=10000):
            version.remove()
