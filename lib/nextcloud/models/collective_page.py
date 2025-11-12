import logging
from enum import Enum
from functools import cached_property
from typing import Any, List, cast

from langchain.text_splitter import RecursiveCharacterTextSplitter
from pydantic import BaseModel

from lib.chromadb import get_unified_collection
from lib.couchdb import couchdb
from lib.nextcloud.models.base import (
    CouchDBModel,
    format_timestamp,
)
from lib.settings import settings

logger = logging.getLogger(__name__)


# Text splitter for chunking long documents
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=800,
    chunk_overlap=100,
    length_function=len,
    separators=["\n\n", "\n", ". ", " ", ""],
)


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
    content: str | None = None


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

    def __hash__(self) -> int:
        return hash(str(self))

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

    @cached_property
    def full_path(self) -> str:
        """Return the full path of the page."""
        return self.ocs.filePath + ("/" + self.ocs.title if not self.is_readme else "")

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
    def get_from_title(cls, title: str) -> "CollectivePage":
        """Load page from title."""
        pages = cls.get_all(selector={"ocs.title": title}, limit=1)
        if not pages:
            raise ValueError(f"CollectivePage with title '{title}' not found")
        return pages[0]

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

    @classmethod
    def get_all(  # type: ignore[override]
        cls, *args, **kwargs
    ) -> List["CollectivePage"]:
        return cast(List[CollectivePage], super().get_all(*args, **kwargs))

    def save(self) -> None:
        from lib.nextcloud.models.group import Group

        super().save()

        # Update ChromaDB collection with text splitting
        if self.ocs and self.content and self.content.strip():
            collection = get_unified_collection()

            try:
                group = Group.get_for_page(self)
            except ValueError:
                group = None

            # Split long documents into chunks for better embeddings
            chunks = text_splitter.split_text(self.content)

            for i in range(len(chunks)):
                collection.upsert(
                    ids=[f"{self.build_id()}_chunk_{i}"],
                    documents=[chunks[i]],
                    metadatas=[
                        {
                            "source_type": self.type,
                            "page_id": self.ocs.id,
                            "title": self.ocs.title,
                            "timestamp": self.ocs.timestamp or 0,
                            "subtype": self.subtype or "",
                            "group_id": group.build_id() if group else "",
                            "chunk_index": i,
                            "total_chunks": len(chunks),
                            "original_doc_id": self.build_id(),
                        },
                    ],
                )

    def delete(self) -> None:
        """Delete the page and all related objects (Decisions, Protocol, Group) and ChromaDB entries."""

        page_id = self.ocs.id if self.ocs else None

        # Delete all documents with matching page_id (Decisions, Protocol, etc.)
        if page_id:
            db = couchdb()

            # Query for all documents with this page_id
            lookup = {
                "selector": {"page_id": page_id},
                "limit": 10000,
            }
            response, results = db.resource.post("_find", json=lookup)
            response.raise_for_status()

            # Delete each related document
            for doc in results.get("docs", []):
                doc_id = doc.get("_id")
                doc_type = doc.get("type", "Unknown")
                if doc_id:
                    try:
                        db.delete(doc_id)
                        logger.info("  Deleted related %s: %s", doc_type, doc_id)
                    except Exception as e:
                        logger.warning(
                            "  Failed to delete %s %s: %s", doc_type, doc_id, e
                        )

            # Delete page chunks from ChromaDB
            collection = get_unified_collection()
            try:
                # Query for all chunks with this page's original_doc_id
                results = collection.get(
                    where={"page_id": page_id}, include=["metadatas"]
                )
                if results and results.get("ids"):
                    chunk_ids = results["ids"]
                    collection.delete(ids=chunk_ids)
                    logger.info(
                        "  Deleted %d ChromaDB chunks for page: %s",
                        len(chunk_ids),
                        self.id,
                    )
            except Exception as e:
                logger.warning(
                    "  Failed to delete ChromaDB chunks for %s: %s", self.id, e
                )

        # Finally, delete the page itself from CouchDB
        super().delete()
