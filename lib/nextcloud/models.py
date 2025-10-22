from datetime import datetime
from typing import Any, List

import pytz
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

    def __str__(self) -> str:
        return f"CollectivePage(id={self.id}, title={self.title})"

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

    @property
    def last_update(self) -> str | None:
        if not self.timestamp:
            return None

        dt_object = datetime.fromtimestamp(self.timestamp)
        tz = pytz.timezone(settings.timezone)
        localized_dt = tz.localize(dt_object)
        return localized_dt.strftime("%c")

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

    @classmethod
    def model_validate(cls, obj: dict, *args, **kwargs) -> "CollectivePage":
        # If _id is present in the input dict, use it for the id field
        for x, y in [("_id", "id"), ("_rev", "rev")]:
            if x in obj and (y not in obj or not obj[y]):
                data = dict(obj)  # copy to avoid mutating caller's dict
                data[y] = data[x]

        return super().model_validate(obj, *args, **kwargs)
