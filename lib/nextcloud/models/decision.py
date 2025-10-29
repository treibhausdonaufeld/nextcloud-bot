from functools import cached_property, lru_cache
from typing import List, cast

from chromadb.errors import NotFoundError

from lib.chromadb import chroma_client
from lib.nextcloud.models.base import CouchDBModel
from lib.nextcloud.models.collective_page import CollectivePage


@lru_cache(maxsize=1)
def get_decision_collection():
    name = "decisions"
    try:
        return chroma_client.get_collection(name=name)
    except NotFoundError:
        return chroma_client.create_collection(name=name)


class Decision(CouchDBModel):
    title: str = ""
    text: str = ""

    date: str
    page_id: int | None = None
    group_id: str = ""
    group_name: str = ""

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
