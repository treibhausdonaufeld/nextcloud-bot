from datetime import datetime
from functools import cached_property
from typing import Any, List

import pytz
from pydantic import BaseModel

from lib.couchdb import couchdb
from lib.settings import settings


class CouchDBModel(BaseModel):
    """Base model for CouchDB documents with _id and _rev fields."""

    id: str | None = None
    rev: str | None = None

    @cached_property
    def type(self) -> str:
        """Return the runtime type name of this model (e.g. 'NCUser')."""
        return type(self).__name__

    def save(self) -> None:
        """Save the current instance to CouchDB."""
        db = couchdb()

        # Prepare the document dict for CouchDB
        doc = self.model_dump()

        doc["type"] = self.type

        if not self.id and hasattr(doc, "build_id"):
            self.id = doc["build_id"]()
        if self.id:
            doc["_id"] = self.id
        if self.rev:
            doc["_rev"] = self.rev

        # Save to CouchDB
        saved_doc = db.save(doc)

        # Update id and rev from the saved document
        self.id = saved_doc.get("_id", self.id)
        self.rev = saved_doc.get("_rev", self.rev)

    def load(self) -> None:
        """Load the latest content from the database into this instance."""
        db = couchdb()

        if not self.id:
            raise ValueError("Cannot load from DB without an id")

        doc = db.get(self.id)
        if not doc:
            raise ValueError(f"No document found in DB with id {self.id}")

        updated_instance = self.model_validate(doc)
        # Copy all model fields from the loaded instance to self
        field_names = type(self).model_fields.keys()
        for name in field_names:
            setattr(self, name, getattr(updated_instance, name, None))

    @classmethod
    def model_validate(cls, obj: dict, *args, **kwargs) -> "CouchDBModel":
        # If _id is present in the input dict, use it for the id field
        for x, y in [("_id", "id"), ("_rev", "rev")]:
            if x in obj and (y not in obj or not obj[y]):
                data = dict(obj)  # copy to avoid mutating caller's dict
                data[y] = data[x]

        return super().model_validate(obj, *args, **kwargs)


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


class CollectivePage(CouchDBModel):
    title: str | None = None
    emoji: str | None = None
    timestamp: int | None = None
    raw: OCSCollectivePage | None = None
    content: str | None = None

    def __str__(self) -> str:
        return f"CollectivePage(id={self.id}, title={self.title})"

    def build_id(self, ocs_page_id: int | None) -> str:
        if not ocs_page_id:
            if self.raw and self.raw.id:
                ocs_page_id = self.raw.id
            else:
                raise ValueError("ocs_page_id is required to build CollectivePage id")
        return f"{self.type}:{settings.nextcloud.collectives_id}:{ocs_page_id}"

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
        instance = cls(
            title=page.title,
            emoji=page.emoji,
            timestamp=page.timestamp,
            raw=page,
        )
        instance.id = instance.build_id(page.id)
        return instance

    @classmethod
    def load_from_raw_id(cls, raw_id: int) -> "CollectivePage":
        """Load the latest content from the database into this instance."""
        if raw_id is None:
            raise ValueError("raw_id is required to load CollectivePage")

        instance = cls()
        instance.id = instance.build_id(raw_id)
        return instance
