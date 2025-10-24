import logging
from typing import List

from lib.nextcloud.models import CouchDBModel

logger = logging.getLogger(__name__)


class Protocol(CouchDBModel):
    group_id: str
    page_id: str

    date: str
    moderated_by: List[str] = []
    protocol_by: List[str] = []
    participants: List[str] = []


class Group(CouchDBModel):
    name: str

    coordination: List[str] = []
    delegate: List[str] = []
    members: List[str] = []


class Decision(CouchDBModel):
    title: str
    text: str

    date: str
    protocol_id: str
    group_id: str

    external_link: str = ""
