import logging

from lib.nextcloud.models import CouchDBModel

logger = logging.getLogger(__name__)


class ProtocolPage(CouchDBModel):
    # allow field population via aliases (JSON uses camelCase keys)
    model_config = {"populate_by_name": True, "extra": "ignore"}

    group: str = ""
