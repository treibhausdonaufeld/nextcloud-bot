from functools import cached_property
from typing import List, cast

from lib.nextcloud.models.base import CouchDBModel
from lib.nextcloud.models.collective_page import CollectivePage


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
