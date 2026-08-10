import logging
from datetime import datetime
from typing import Any, ClassVar, List, Optional, Type, TypeVar

import edgy
import pytz

from app.db import registry, run_db
from app.settings import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound="BaseDBModel")


def format_timestamp(timestamp: int | None) -> str | None:
    if not timestamp:
        return None

    dt_object = datetime.fromtimestamp(timestamp)
    tz = pytz.timezone(settings.timezone)
    localized_dt = tz.localize(dt_object)
    return localized_dt.strftime("%c")


def format_date(timestamp: int | None) -> str | None:
    """Format a unix timestamp as an ISO date in the configured timezone."""
    if not timestamp:
        return None

    tz = pytz.timezone(settings.timezone)
    return datetime.fromtimestamp(timestamp, tz).strftime("%Y-%m-%d")


class BaseDBModel(edgy.Model):
    """Base model with a synchronous facade over Edgy's async API.

    The parsing pipeline, worker and CLI are synchronous, so models expose
    sync methods (`store`, `remove`, `fetch`, ...) that dispatch to the
    dedicated database loop via `run_db`. Subclasses hook persistence side
    effects (search index, mentions, cascades) into `after_store` /
    `before_remove` instead of overriding `store`/`remove`.
    """

    id: int = edgy.BigIntegerField(primary_key=True, autoincrement=True)
    updated_at: int | None = edgy.BigIntegerField(null=True, index=True)

    # Fields identifying a row independently of its autoincrement id; used by
    # `store()` to update instead of insert when a row already exists.
    natural_key_fields: ClassVar[tuple[str, ...]] = ()

    class Meta:
        abstract = True
        registry = registry

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Edgy leaves fields with defaults unset until insert time; the
        # parsing code expects plain attribute access on fresh instances,
        # so materialize defaults eagerly.
        for name, field in self.meta.fields.items():
            if name in self.__dict__:
                continue
            if field.has_default():
                default = field.get_default_value()  # type: ignore[attr-defined]
                if default is None and not getattr(field, "null", False):
                    continue  # e.g. the autoincrement primary key
                setattr(self, name, default)
            elif getattr(field, "null", False):
                setattr(self, name, None)

    def store(self, skip_set_updated_at: bool = False) -> None:
        """Save this instance (insert or update by natural key)."""
        if not skip_set_updated_at:
            self.updated_at = int(datetime.now().timestamp())

        if getattr(self, "id", None) is None and self.natural_key_fields:
            existing = type(self).fetch_one(
                **{f: getattr(self, f) for f in self.natural_key_fields}
            )
            if existing is not None:
                self.id = existing.id

        run_db(self.save())
        self.after_store()

    def remove(self) -> None:
        """Delete this instance from the database."""
        self.before_remove()
        run_db(self.delete())
        logger.info("Deleted %s id=%s", type(self).__name__, self.id)

    def after_store(self) -> None:
        """Hook for persistence side effects (search index, mentions, ...)."""

    def before_remove(self) -> None:
        """Hook for cascading deletes and index cleanup."""

    @classmethod
    def fetch(
        cls: Type[T],
        limit: int | None = None,
        offset: int | None = None,
        order_by: str = "-updated_at",
        **filters: Any,
    ) -> List[T]:
        """Load models matching the given filters (Edgy filter syntax)."""

        async def _fetch() -> List[T]:
            qs = cls.query.filter(**filters) if filters else cls.query.all()
            if order_by:
                qs = qs.order_by(order_by)
            if offset:
                qs = qs.offset(offset)
            if limit:
                qs = qs.limit(limit)
            return await qs.all()

        return run_db(_fetch())

    @classmethod
    def fetch_one(cls: Type[T], **filters: Any) -> Optional[T]:
        async def _fetch_one() -> Optional[T]:
            return await cls.query.filter(**filters).first()

        return run_db(_fetch_one())

    @classmethod
    def count(cls, **filters: Any) -> int:
        async def _count() -> int:
            qs = cls.query.filter(**filters) if filters else cls.query.all()
            return await qs.count()

        return run_db(_count())
