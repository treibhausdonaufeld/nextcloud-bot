import logging
from datetime import datetime
from enum import Enum
from functools import cached_property
from typing import Any, List, cast

import pytz
from pycouchdb.exceptions import Conflict
from pydantic import BaseModel

from lib.couchdb import couchdb
from lib.settings import settings

logger = logging.getLogger(__name__)


def format_timestamp(timestamp: int | None) -> str | None:
    if not timestamp:
        return None

    dt_object = datetime.fromtimestamp(timestamp)
    tz = pytz.timezone(settings.timezone)
    localized_dt = tz.localize(dt_object)
    return localized_dt.strftime("%c")


class CouchDBModel(BaseModel):
    """Base model for CouchDB documents with _id and _rev fields."""

    id: str | None = None
    rev: str | None = None

    updated_at: int | None = None

    def __init__(self, **data: Any):
        if isinstance(data, dict):
            for x, y in [("_id", "id"), ("_rev", "rev")]:
                if x in data:
                    data[y] = data[x]

        return super().__init__(**data)

    @cached_property
    def type(self) -> str:
        """Return the runtime type name of this model (e.g. 'NCUser')."""
        return type(self).__name__

    def save(self) -> None:
        """Save the current instance to CouchDB."""
        db = couchdb()

        self.updated_at = int(datetime.now().timestamp())

        if not self.id and hasattr(self, "build_id"):
            self.id = getattr(self, "build_id")()

        # Prepare the document dict for CouchDB
        doc = self.model_dump()

        doc["type"] = self.type

        if self.id:
            doc["_id"] = self.id
        if self.rev:
            doc["_rev"] = self.rev

        # Save to CouchDB
        try:
            saved_doc = db.save(doc)
        except Conflict:
            # load once again from db and try to save again
            existing_doc = db.get(self.id)
            self.rev = doc["_rev"] = existing_doc.get("_rev")
            saved_doc = db.save(doc)

        # Update id and rev from the saved document
        self.id = saved_doc.get("_id", self.id)
        self.rev = saved_doc.get("_rev", self.rev)

    def delete(self) -> None:
        """Delete the current instance from CouchDB."""
        db = couchdb()

        if not self.id:
            raise ValueError("Cannot delete document without id")

        db.delete(self.id)
        logger.info("Deleted document %s from CouchDB", self.id)

    @classmethod
    def get(cls, doc_id: str) -> "CouchDBModel":
        """Get a document by its id from CouchDB."""
        db = couchdb()

        if not doc_id:
            raise ValueError("doc_id is required to get document")

        doc = db.get(doc_id)
        return cls(**doc)

    @classmethod
    def get_all(
        cls, limit: int = 100, sort: List[str | dict] = [{"updated_at": "desc"}]
    ) -> List["CouchDBModel"]:
        """Load all documents of this model type from CouchDB."""
        db = couchdb()

        lookup = {
            "selector": {"type": cls.__name__},
            "sort": sort,
            "limit": limit,
        }
        response, results = db.resource.post("_find", json=lookup)
        response.raise_for_status()

        return [cls(**d) for d in results.get("docs", [])]

    @property
    def formatted_updated_at(self) -> str | None:
        return format_timestamp(self.updated_at)


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
