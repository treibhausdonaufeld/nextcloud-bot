from lib.nextcloud.models.base import CouchDBModel


class Decision(CouchDBModel):
    title: str
    text: str

    date: str
    protocol_id: str
    group_id: str

    external_link: str = ""
