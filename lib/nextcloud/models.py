from typing import Any, List

from pydantic import BaseModel

from lib.settings import settings


class OCSCollectivePage(BaseModel):
    id: int
    slug: str | None = None
    lastUserId: str | None = None
    lastUserDisplayName: str | None = None
    emoji: str | None = None
    subpageOrder: List[Any] = []
    isFullWidth: bool | None = False
    tags: List[str] = []
    trashTimestamp: int | None = None
    title: str | None = None
    timestamp: int | None = None
    size: int | None = None
    fileName: str | None = None
    filePath: str | None = None
    filePathString: str | None = None
    collectivePath: str | None = None
    parentId: int | None = None
    shareToken: str | None = None


class CollectivePage(BaseModel):
    id: str | None = None
    rev: str | None = None
    type: str = "collective_page"
    title: str | None = None
    emoji: str | None = None
    timestamp: int | None = None
    raw: OCSCollectivePage | None = None
    content: str | None = None

    @property
    def collective_name(self) -> str | None:
        if not self.raw or not self.raw.collectivePath:
            return None
        return self.raw.collectivePath.split("/")[1]

    @property
    def url(self) -> str | None:
        if not self.raw or not self.raw.collectivePath or not self.raw.slug:
            return None

        return (
            str(settings.nextcloud.base_url).rstrip("/")
            + f"/apps/collectives/{self.collective_name}-{settings.nextcloud.collectives_id}"
            + f"/{self.raw.slug}-{self.raw.id}"
        )

    @classmethod
    def from_ocs_page(cls, page: OCSCollectivePage) -> "CollectivePage":
        doc_id = f"collective:{settings.nextcloud.collectives_id}:{page.id}"
        return cls(
            id=doc_id,
            title=page.title,
            emoji=page.emoji,
            timestamp=page.timestamp,
            raw=page,
        )
