import datetime
import logging

from lib.nextcloud.collectives_loader import (
    fetch_and_store_all_pages,
)
from lib.nextcloud.collectives_parser import parse_content
from lib.nextcloud.models.collective_page import CollectivePage
from lib.nextcloud.models.decision import Decision

logger = logging.getLogger(__name__)


def main():
    # first load config from nextcloud
    # try:
    #     config_page = CollectivePage.get_from_page_id(
    #         page_id=settings.nextcloud.configuration_page_id
    #     )
    # except NotFound:
    #     ocs_page = fetch_ocs_collective_page(
    #         page_id=settings.nextcloud.configuration_page_id
    #     )
    #     config_page = CollectivePage(ocs=ocs_page)
    #     config_page.save()

    fetch_and_store_all_pages()

    for d in Decision.get_all():
        logger.info("Existing decision: %s", d.title)
        d.delete()

    # for page in updated_pages:
    for page in CollectivePage.get_all():
        logger.info("Processing page: %s", page.ocs.title)
        parse_content(page)
        page.save()

    # for group in Group.get_all():
    #     group.delete()
    # for p in Protocol.get_all():
    #     p.delete()

    # parse_pages()

    # NCUserList()
    # userlist.update_from_nextcloud()

    # group = Group.get_by_name("UG IT")
    # group.update_from_page()

    # only send reminder out of sleeping hours
    if not (8 < datetime.datetime.now().hour < 20):
        return


if __name__ == "__main__":
    main()
