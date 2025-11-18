import logging
import time
from datetime import datetime

import click

from lib.chromadb import UNIFIED_COLLECTION_NAME, chroma_client
from lib.mail.fetcher import MailFetcher
from lib.nextcloud.avatar_fetcher import AvatarFetcher
from lib.nextcloud.calendar_notifier import Notifier
from lib.nextcloud.collectives_loader import fetch_and_store_all_pages
from lib.nextcloud.collectives_parser import parse_groups, parse_protocols
from lib.nextcloud.config import BotConfig
from lib.nextcloud.deck_reminder import DeckReminder
from lib.nextcloud.models.collective_page import CollectivePage
from lib.nextcloud.models.decision import Decision
from lib.nextcloud.models.group import Group
from lib.nextcloud.models.protocol import Protocol
from lib.nextcloud.models.user import NCUserList
from lib.settings import set_language, settings

logger = logging.getLogger(__name__)

# reduce logging for httpx package to WARNING
# logging.getLogger("httpx").setLevel(logging.WARNING)

set_language(settings.default_language)


def delete_all_parsed_data():
    for group in Group.get_all(limit=1000):
        group.delete()
    for p in Protocol.get_all(limit=1000):
        p.delete()
    for d in Decision.get_all(limit=1000):
        d.delete()


@click.command()
@click.option("--loop", is_flag=True, default=False, help="Run main() in a loop")
@click.option(
    "--update-all", is_flag=True, default=False, help="Update and re-parse all pages"
)
@click.option(
    "--update-pages",
    default="",
    help="Comma-separated list of collectives page ids to fetch and force-update from Nextcloud",
)
@click.option(
    "--clear-chromadb", is_flag=True, default=False, help="Clear all chromadb data"
)
@click.option(
    "--clear-parsed-data", is_flag=True, default=False, help="Clear all chromadb data"
)
def main(
    loop: bool,
    update_all: bool,
    clear_chromadb: bool,
    clear_parsed_data: bool,
    update_pages: str,
):
    logger.debug("Starting runner")

    if clear_chromadb:
        logger.info("Clearing all ChromaDB data...")
        chroma_client.delete_collection(UNIFIED_COLLECTION_NAME)
        return

    if clear_parsed_data:
        logger.info("Clearing all parsed data...")
        delete_all_parsed_data()
        return

    fetcher = MailFetcher()

    while True:
        # Update all users from Nextcloud
        userlist = NCUserList()
        try:
            userlist.update_from_nextcloud()
        except Exception as e:
            logger.error("Failed to update users from Nextcloud: %s", e, exc_info=True)

        # Default: fetch pages changed in Nextcloud and store them
        updated_pages = fetch_and_store_all_pages()

        if update_all:
            updated_pages = CollectivePage.get_all(limit=1000)
        elif update_pages:
            ids = [p.strip() for p in update_pages.split(",") if p.strip()]
            updated_pages = [
                CollectivePage.get_from_page_id(page_id=int(pid)) for pid in ids
            ]

        if update_pages or update_all:
            # force save of all pages
            for page in updated_pages:
                page.save()

        for page in updated_pages:
            parse_groups(page)

        for page in updated_pages:
            parse_protocols(page)

        config = BotConfig.load_config()

        if config.avatare.fetch_avatar:
            AvatarFetcher(config.avatare).fetch_images(userlist)

        if settings.mailinglist.imap_server:
            fetcher.fetch_maildata(userlist, config.mailer)

        Notifier(config=config.calendar_notifier).notify_upcoming_events()
        DeckReminder(config=config.deck_reminder).remind_card_due_dates()

        if not loop:
            break

        if (
            datetime.now().hour >= config.quiet_hours_start
            or datetime.now().hour < config.quiet_hours_end
        ):
            # calculate sleep time until quiet hours end
            if datetime.now().hour >= config.quiet_hours_start:
                hours_until_quiet_end = (
                    24 - datetime.now().hour
                ) + config.quiet_hours_end
            else:
                hours_until_quiet_end = config.quiet_hours_end - datetime.now().hour
            sleep_minutes = hours_until_quiet_end * 60
            logger.info("Quiet hours active, sleeping for %d minutes...", sleep_minutes)
            time.sleep(sleep_minutes * 60)
        else:
            logger.info("Sleeping for %d minutes...", config.sleep_minutes)
            time.sleep(config.sleep_minutes * 60)


if __name__ == "__main__":
    main()
