from enum import Enum
from typing import Any, List, cast

from pydantic import BaseModel

from lib.nextcloud.models.base import (
    CouchDBModel,
    format_timestamp,
)
from lib.settings import settings


class OCSCollectivePage(BaseModel):
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


class PageSubtype(str, Enum):
    GROUP = "group"
    PROTOCOL = "protocol"


class CollectivePage(CouchDBModel):
    ocs: OCSCollectivePage = OCSCollectivePage()
    content: str | None = None

    subtype: PageSubtype | None = None
    tags: List[str] = []

    def __str__(self) -> str:
        return f"CollectivePage(id={self.id}, title={self.title})"

    def build_id(self) -> str:
        if not self.ocs or not self.ocs.id:
            raise ValueError("ocs.id is required to build CollectivePage id")
        return f"{self.__class__.__name__}:{settings.nextcloud.collectives_id}:{self.ocs.id}"

    @property
    def title(self) -> str:
        return self.ocs.title if self.ocs and self.ocs.title else ""

    @property
    def timestamp(self) -> int | None:
        return self.ocs.timestamp if self.ocs and self.ocs.timestamp else None

    @property
    def is_readme(self) -> bool:
        return (
            self.ocs.fileName.lower() == "readme.md"
            if self.ocs and self.ocs.fileName
            else False
        )

    @property
    def collective_name(self) -> str | None:
        if not self.ocs or not self.ocs.collectivePath:
            return None
        return self.ocs.collectivePath.split("/")[1]

    @property
    def url(self) -> str | None:
        if not self.ocs or not self.ocs.collectivePath or not self.ocs.slug:
            return None

        return (
            str(settings.nextcloud.base_url).rstrip("/")
            + f"/apps/collectives/{self.collective_name}-{settings.nextcloud.collectives_id}"
            + f"/{self.ocs.slug}-{self.ocs.id}"
        )

    @property
    def formatted_timestamp(self) -> str | None:
        return format_timestamp(self.timestamp)

    @classmethod
    def get_from_page_id(cls, page_id: int) -> "CollectivePage":
        """Load the latest content from the database into this instance."""
        return cast(
            CollectivePage,
            cls.get(CollectivePage(ocs=OCSCollectivePage(id=page_id)).build_id()),
        )

    @classmethod
    def get(cls, doc_id: str) -> "CollectivePage":
        return cast(CollectivePage, super().get(doc_id))
