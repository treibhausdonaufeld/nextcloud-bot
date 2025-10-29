import logging
import threading
from collections import OrderedDict
from datetime import datetime
from functools import cached_property
from typing import Any, ClassVar, List, Type, TypeVar

import pytz
from pycouchdb.exceptions import Conflict
from pydantic import BaseModel

from lib.couchdb import couchdb
from lib.settings import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


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

    # class-level LRU cache (shared across subclasses)
    _cache_lock: ClassVar[threading.RLock] = threading.RLock()
    _instance_cache: ClassVar[OrderedDict] = OrderedDict()
    _cache_max_size: ClassVar[int] = 500  # default max entries

    @classmethod
    def set_cache_size(cls, size: int) -> None:
        """Adjust the maximum number of cached instances."""
        with CouchDBModel._cache_lock:
            CouchDBModel._cache_max_size = max(0, int(size))
            # Immediately trim if needed
            while len(CouchDBModel._instance_cache) > CouchDBModel._cache_max_size:
                CouchDBModel._instance_cache.popitem(last=False)

    @classmethod
    def _cache_get(cls, doc_id: str):
        if not doc_id:
            return None
        with CouchDBModel._cache_lock:
            inst = CouchDBModel._instance_cache.get(doc_id)
            if inst:
                # mark as recently used
                CouchDBModel._instance_cache.move_to_end(doc_id)
            return inst

    @classmethod
    def _cache_add(cls, instance: "CouchDBModel") -> None:
        if not instance.id:
            return
        with CouchDBModel._cache_lock:
            CouchDBModel._instance_cache[instance.id] = instance
            CouchDBModel._instance_cache.move_to_end(instance.id)
            # trim LRU entries
            while len(CouchDBModel._instance_cache) > CouchDBModel._cache_max_size:
                CouchDBModel._instance_cache.popitem(last=False)

    @classmethod
    def _cache_invalidate(cls, doc_id: str) -> None:
        if not doc_id:
            return
        with CouchDBModel._cache_lock:
            CouchDBModel._instance_cache.pop(doc_id, None)

    @classmethod
    def clear_cache(cls) -> None:
        with CouchDBModel._cache_lock:
            CouchDBModel._instance_cache.clear()

    @cached_property
    def type(self) -> str:
        """Return the runtime type name of this model (e.g. 'NCUser')."""
        return type(self).__name__

    def build_id(self) -> str:
        """Build the document id."""
        raise NotImplementedError

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

        # update cache
        self._cache_add(self)

    def delete(self) -> None:
        """Delete the current instance from CouchDB."""
        db = couchdb()

        if not self.id:
            raise ValueError("Cannot delete document without id")

        db.delete(self.id)
        # invalidate cache
        self._cache_invalidate(self.id)
        logger.info("Deleted document %s from CouchDB", self.id)

    @classmethod
    def get(cls, doc_id: str) -> "CouchDBModel":
        """Get a document by its id from CouchDB."""
        db = couchdb()

        if not doc_id:
            raise ValueError("doc_id is required to get document")

        # check cache first
        cached = cls._cache_get(doc_id)
        if cached and isinstance(cached, cls):
            return cached

        doc = db.get(doc_id)
        inst = cls(**doc)
        cls._cache_add(inst)
        return inst

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

    @classmethod
    def get_by(cls: Type[T], key: str, value: Any) -> List[T]:
        """Get a list of models by a key-value pair."""
        db = couchdb()
        lookup = {"selector": {"type": cls.__name__, key: value}}
        response, results = db.resource.post("_find", json=lookup)
        response.raise_for_status()
        return [cls(**doc) for doc in results.get("docs", [])]
