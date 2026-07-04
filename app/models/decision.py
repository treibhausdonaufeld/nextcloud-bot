from typing import TYPE_CHECKING, List, Optional

import edgy

from app.db import remove_from_search_index, update_search_index
from app.models.base import BaseDBModel

if TYPE_CHECKING:
    from app.models.collective_page import CollectivePage


class Decision(BaseDBModel):
    title: str = edgy.TextField(default="")
    text: str = edgy.TextField(default="")

    date: str = edgy.CharField(max_length=64)
    page_id: int | None = edgy.BigIntegerField(null=True, index=True)
    group_name: str = edgy.CharField(max_length=255, default="", index=True)

    valid_until: str = edgy.CharField(max_length=255, default="")
    objections: str = edgy.TextField(default="")

    external_link: str = edgy.CharField(max_length=1024, default="")

    # Surrounding context from the protocol (the agenda heading above the
    # decision block) — indexed for search and shown in the detail view.
    context: str = edgy.TextField(default="")

    # Deterministic key mirroring the old CouchDB document id, so re-parsing
    # a protocol or re-importing an XLSX updates instead of duplicating.
    # Computed in store(); the default only exists so instances can be
    # constructed without it.
    natural_key: str = edgy.CharField(max_length=255, unique=True, default="")

    natural_key_fields = ("natural_key",)

    class Meta:
        tablename = "decisions"

    def build_natural_key(self) -> str:
        if not self.title and not self.text:
            raise ValueError("Decision must have either a title or text to build ID")
        head = self.title[0:20] if self.title else self.text[0:20]
        return f"{self.page_id}:{head}"

    def __contains__(self, item: str) -> bool:
        item_lower = item.lower().strip()
        return item_lower in self.title.lower() or item_lower in self.text.lower()

    @property
    def page(self) -> Optional["CollectivePage"]:
        from app.models.collective_page import CollectivePage

        if self.page_id:
            return CollectivePage.get_from_page_id_or_none(self.page_id)
        return None

    @classmethod
    def paginate(
        cls,
        limit: int,
        skip: int,
        order_by: str = "-updated_at",
        **filters,
    ) -> List["Decision"]:
        return cls.fetch(limit=limit, offset=skip, order_by=order_by, **filters)

    def store(self, skip_set_updated_at: bool = False) -> None:
        self.natural_key = self.build_natural_key()
        super().store(skip_set_updated_at=skip_set_updated_at)

    def after_store(self) -> None:
        if self.title or self.text:
            # index the decision together with its context (agenda heading,
            # objections, group, date) so searches match on surroundings too
            body_parts = (
                self.title,
                self.text,
                self.objections,
                self.context,
                self.group_name,
                self.date,
            )
            body = " ".join(part for part in body_parts if part)
            update_search_index("decision", str(self.id), self.title, body)

    def before_remove(self) -> None:
        remove_from_search_index("decision", str(self.id))
