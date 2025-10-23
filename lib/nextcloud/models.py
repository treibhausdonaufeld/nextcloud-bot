from datetime import datetime
from enum import Enum
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

    updated_at: int | None = None

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

        self.updated_at = int(datetime.now().timestamp())

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
        new_obj = super().model_validate(obj, *args, **kwargs)

        # hard overwrite id and rev from _id and _rev fields
        for x, y in [("_id", "id"), ("_rev", "rev")]:
            setattr(new_obj, y, obj[x])

        return new_obj

    @classmethod
    def load_all(cls, limit: int = 100) -> List["CouchDBModel"]:
        """Load all documents of this model type from CouchDB."""
        db = couchdb()

        lookup = {
            "selector": {"type": cls.__name__},
            "sort": [{"updated_at": "desc"}],
            "limit": limit,
        }
        response, results = db.resource.post("_find", json=lookup)
        response.raise_for_status()

        return [cls(**d) for d in results.get("docs", [])]


class OCSCollectivePage(BaseModel):
    id: int = 0
    slug: str | None = None
    lastUserId: str | None = None
    lastUserDisplayName: str | None = None
    emoji: str | None = None
    subpageOrder: List[Any] = []
    isFullWidth: bool | None = False
    tags: List[str] = []
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
    title: str = ""
    timestamp: int | None = None
    ocs: OCSCollectivePage = OCSCollectivePage()
    content: str | None = None

    subtype: PageSubtype | None = None
    date: str | None = None
    moderated_by: str | None = None
    protocol_by: str | None = None
    participants: List[str] = []
    tags: List[str] = []

    def __str__(self) -> str:
        return f"CollectivePage(id={self.id}, title={self.title})"

    def build_id(self, ocs_page_id: int | None) -> str:
        if not ocs_page_id:
            if self.ocs and self.ocs.id:
                ocs_page_id = self.ocs.id
            else:
                raise ValueError("ocs_page_id is required to build CollectivePage id")
        return f"{self.type}:{settings.nextcloud.collectives_id}:{ocs_page_id}"

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
            timestamp=page.timestamp,
            ocs=page,
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
        instance.load()
        return instance
