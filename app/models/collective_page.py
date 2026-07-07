import logging
from enum import Enum
from typing import Any, List, Optional

import edgy
from pydantic import BaseModel

from app.db import remove_from_search_index, update_search_index
from app.models.base import BaseDBModel, format_timestamp
from app.settings import settings

logger = logging.getLogger(__name__)


class OCSCollectivePage(BaseModel):
    """Raw page payload as returned by the Nextcloud Collectives OCS API."""

    id: int = 0
    slug: str | None = None
    lastUserId: str | None = None
    lastUserDisplayName: str | None = None
    emoji: str | None = None
    subpageOrder: List[Any] = []
    isFullWidth: bool | None = False
    tags: List[int] = []
    trashTimestamp: int | None = None
    title: str = ""
    timestamp: int | None = None
    size: int | None = None
    fileName: str = ""
    filePath: str = ""
    filePathString: str = ""
    collectivePath: str = ""
    parentId: int | None = None
    shareToken: str | None = None
    content: str | None = None


class PageSubtype(str, Enum):
    GROUP = "group"
    PROTOCOL = "protocol"


class CollectivePage(BaseDBModel):
    page_id: int = edgy.BigIntegerField(unique=True)
    collectives_id: int = edgy.BigIntegerField(default=0)

    title: str = edgy.CharField(max_length=512, default="")
    slug: str | None = edgy.CharField(max_length=512, null=True)
    file_name: str = edgy.CharField(max_length=512, default="")
    file_path: str = edgy.CharField(max_length=1024, default="")
    collective_path: str = edgy.CharField(max_length=1024, default="")
    last_user_id: str | None = edgy.CharField(max_length=255, null=True)
    emoji: str | None = edgy.CharField(max_length=32, null=True)
    timestamp: int | None = edgy.BigIntegerField(null=True, index=True)
    size: int | None = edgy.BigIntegerField(null=True)
    parent_id: int | None = edgy.BigIntegerField(null=True)

    content: str | None = edgy.TextField(null=True)
    subtype: str | None = edgy.CharField(max_length=32, null=True)

    natural_key_fields = ("page_id",)

    class Meta:
        tablename = "collective_pages"

    def __str__(self) -> str:
        return f"CollectivePage(id={self.id}, title={self.title})"

    def __hash__(self) -> int:
        return hash(str(self))

    @property
    def is_readme(self) -> bool:
        return self.file_name.lower() == "readme.md" if self.file_name else False

    @property
    def full_path(self) -> str:
        """Return the full path of the page."""
        return self.file_path + ("/" + self.title if not self.is_readme else "")

    @property
    def collective_name(self) -> str | None:
        if not self.collective_path:
            return None
        return self.collective_path.split("/")[1]

    @property
    def url(self) -> str | None:
        if not self.collective_path or not self.slug:
            return None

        return (
            str(settings.nextcloud.base_url).rstrip("/")
            + f"/apps/collectives/{self.collective_name}-{settings.nextcloud.collectives_id}"
            + f"/{self.slug}-{self.page_id}"
        )

    @property
    def formatted_timestamp(self) -> str | None:
        return format_timestamp(self.timestamp)

    @classmethod
    def from_ocs(cls, ocs: OCSCollectivePage) -> "CollectivePage":
        """Build an (unsaved) page from an OCS payload."""
        page = cls(page_id=ocs.id)
        page.apply_ocs(ocs)
        return page

    def apply_ocs(self, ocs: OCSCollectivePage) -> None:
        self.page_id = ocs.id
        self.collectives_id = settings.nextcloud.collectives_id
        self.title = ocs.title or ""
        self.slug = ocs.slug
        self.file_name = ocs.fileName or ""
        self.file_path = ocs.filePath or ""
        self.collective_path = ocs.collectivePath or ""
        self.last_user_id = ocs.lastUserId
        self.emoji = ocs.emoji
        self.timestamp = ocs.timestamp
        self.size = ocs.size
        self.parent_id = ocs.parentId
        if ocs.content is not None:
            self.content = ocs.content

    @classmethod
    def get_from_title(cls, title: str) -> "CollectivePage":
        """Load page from title."""
        page = cls.fetch_one(title=title)
        if page is None:
            raise ValueError(f"CollectivePage with title '{title}' not found")
        return page

    @classmethod
    def get_from_page_id(cls, page_id: int) -> "CollectivePage":
        """Load a page by its Nextcloud page id."""
        page = cls.fetch_one(page_id=page_id)
        if page is None:
            raise ValueError(f"CollectivePage with page_id '{page_id}' not found")
        return page

    @classmethod
    def get_from_page_id_or_none(cls, page_id: int) -> Optional["CollectivePage"]:
        return cls.fetch_one(page_id=page_id)

    def after_store(self) -> None:
        """Keep the mentions table and the full-text index in sync."""
        from app.models.mention import Mention

        Mention.rebuild_for_page(self.page_id, self.content or "")
        if self.content and self.content.strip():
            update_search_index("page", str(self.page_id), self.title, self.content)
        else:
            remove_from_search_index("page", str(self.page_id))

    def before_remove(self) -> None:
        """Cascade to protocol, decisions, versions, media, mentions, index."""
        from app.models.decision import Decision
        from app.models.mention import Mention
        from app.models.protocol import Protocol
        from app.models.protocol_media import ProtocolMedia
        from app.models.protocol_version import ProtocolVersion

        for protocol in Protocol.fetch(page_id=self.page_id):
            # Protocol.before_remove deletes its decisions
            protocol.remove()
        for decision in Decision.fetch(page_id=self.page_id):
            decision.remove()
        ProtocolVersion.remove_for_page(self.page_id)
        ProtocolMedia.remove_for_page(self.page_id)
        Mention.rebuild_for_page(self.page_id, "")
        remove_from_search_index("page", str(self.page_id))
