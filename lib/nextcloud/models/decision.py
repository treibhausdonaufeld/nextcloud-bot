from functools import cached_property, lru_cache
from typing import List, cast

from lib.chromadb import chroma_client
from lib.chromadb import embedding_function as ef
from lib.couchdb import couchdb
from lib.nextcloud.models.base import CouchDBModel
from lib.nextcloud.models.collective_page import CollectivePage


@lru_cache(maxsize=1)
def get_decision_collection():
    return chroma_client.get_or_create_collection("decisions", embedding_function=ef)  # type: ignore


class Decision(CouchDBModel):
    title: str = ""
    text: str = ""

    date: str
    page_id: int | None = None
    group_id: str = ""
    group_name: str = ""

    valid_until: str = ""
    objections: str = ""

    external_link: str = ""

    def build_id(self) -> str:
        if not self.title and not self.text:
            raise ValueError("Decision must have either a title or text to build ID")
        return f"{self.__class__.__name__}:{self.page_id}:{self.title[0:20] if self.title else self.text[0:20]}"

    def __contains__(self, item: str) -> bool:
        item_lower = item.lower().strip()
        return item_lower in self.title.lower() or item_lower in self.text.lower()

    @cached_property
    def page(self) -> CollectivePage | None:
        if self.page_id:
            try:
                return CollectivePage.get_from_page_id(self.page_id)
            except ValueError:
                return None
        return None

    @classmethod
    def get_all(cls, *args, **kwargs) -> List["Decision"]:
        return cast(List[Decision], super().get_all(*args, **kwargs))

    @classmethod
    def paginate(
        cls,
        limit: int,
        skip: int,
        sort: List[str | dict] = [{"updated_at": "desc"}],
        selector: dict | None = None,
    ) -> List["Decision"]:
        db = couchdb()

        lookup = {
            "selector": {"type": cls.__name__} | (selector or {}),
            "sort": sort,
            "limit": limit,
            "skip": skip,
        }
        response, results = db.resource.post("_find", json=lookup)
        response.raise_for_status()

        return [cls(**d) for d in results.get("docs", [])]

    def save(self) -> None:
        super().save()

        # Update ChromaDB collection
        if self.title or self.text:
            collection = get_decision_collection()

            collection.upsert(
                ids=[self.build_id()],
                documents=[self.title + self.text],
                metadatas=[
                    {
                        "page_id": self.page_id,
                        "title": self.title,
                        "date": self.date,
                        "group_name": self.group_name,
                    },
                ],
            )

    def delete(self) -> None:
        # Remove from ChromaDB collection
        collection = get_decision_collection()
        collection.delete(ids=[self.build_id()])

        super().delete()
