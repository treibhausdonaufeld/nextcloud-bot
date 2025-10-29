from typing import List, cast

from lib.nextcloud.models.base import CouchDBModel


class Decision(CouchDBModel):
    title: str = ""
    text: str = ""

    date: str
    protocol_id: str = ""
    group_id: str = ""
    group_name: str = ""

    external_link: str = ""

    def build_id(self) -> str:
        return f"{self.__class__.__name__}:{self.protocol_id}:{self.title}"

    @classmethod
    def get_all(cls, *args, **kwargs) -> List["Decision"]:
        return cast(List[Decision], super().get_all(*args, **kwargs))
