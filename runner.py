import logging
import time
from datetime import datetime

import click
import requests

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

set_language(settings.default_language)


SLEEP_MINUTES_DEFAULT = 60

# Network-related exceptions to catch and retry
NETWORK_EXCEPTIONS = (
    requests.exceptions.RequestException,
    ConnectionError,
    TimeoutError,
    OSError,
)


def delete_all_parsed_data():
    """Delete all parsed groups, protocols, and decisions."""
    for group in Group.get_all(limit=1000):
        group.delete()
    for p in Protocol.get_all(limit=1000):
        p.delete()
    for d in Decision.get_all(limit=1000):
        d.delete()


def get_updated_pages(update_all: bool, update_pages: str) -> list[CollectivePage]:
    """Fetch and return the list of pages to process."""
    updated_pages = fetch_and_store_all_pages()

    if update_all:
        return CollectivePage.get_all(limit=1000)

    if update_pages:
        ids = [p.strip() for p in update_pages.split(",") if p.strip()]
        return [CollectivePage.get_from_page_id(page_id=int(pid)) for pid in ids]

    return updated_pages


def process_pages(updated_pages: list[CollectivePage], force_save: bool):
    """Process updated pages: save if needed, then parse groups and protocols."""
    if force_save:
        for page in updated_pages:
            page.save()

    for page in updated_pages:
        parse_groups(page)

    for page in updated_pages:
        parse_protocols(page)


def run_periodic_tasks(userlist: NCUserList, fetcher: MailFetcher, config: BotConfig):
    """Run periodic tasks like avatar fetching, mail fetching, and notifications."""
    if config.avatare.fetch_avatar:
        AvatarFetcher(config.avatare).fetch_images(userlist)

    if settings.mailinglist.imap_server:
        fetcher.fetch_maildata(userlist, config.mailer)

    Notifier(config=config.calendar_notifier).notify_upcoming_events()
    DeckReminder(config=config.deck_reminder).remind_card_due_dates()


def run_iteration(
    fetcher: MailFetcher, update_all: bool, update_pages: str
) -> BotConfig:
    """Run a single iteration of the main loop. Returns the loaded config."""
    userlist = NCUserList()
    userlist.update_from_nextcloud()

    updated_pages = get_updated_pages(update_all, update_pages)
    force_save = bool(update_pages or update_all)
    process_pages(updated_pages, force_save)

    config = BotConfig.load_config()
    run_periodic_tasks(userlist, fetcher, config)

    return config


def calculate_sleep_duration(config: BotConfig) -> int:
    """Calculate how long to sleep in minutes, accounting for quiet hours."""
    now = datetime.now()

    if now.hour >= config.quiet_hours_start or now.hour < config.quiet_hours_end:
        if now.hour >= config.quiet_hours_start:
            hours_until_quiet_end = (24 - now.hour) + config.quiet_hours_end
        else:
            hours_until_quiet_end = config.quiet_hours_end - now.hour
        sleep_minutes = hours_until_quiet_end * 60
        logger.info("Quiet hours active, sleeping for %d minutes...", sleep_minutes)
    else:
        sleep_minutes = config.sleep_minutes
        logger.info("Sleeping for %d minutes...", sleep_minutes)

    return sleep_minutes


def get_sleep_minutes_safe() -> int:
    """Get sleep minutes from config, with fallback on failure."""
    try:
        config = BotConfig.load_config()
        return config.sleep_minutes
    except Exception:
        return SLEEP_MINUTES_DEFAULT  # fallback default


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
        try:
            config = run_iteration(fetcher, update_all, update_pages)
        except NETWORK_EXCEPTIONS as e:
            logger.warning(
                "Network error occurred, will retry after sleep: %s: %s",
                type(e).__name__,
                str(e),
            )
            if not loop:
                raise
            sleep_minutes = get_sleep_minutes_safe()
            logger.info("Sleeping for %d minutes before retry...", sleep_minutes)
            time.sleep(sleep_minutes * 60)
            continue

        if not loop:
            break

        sleep_minutes = calculate_sleep_duration(config)
        time.sleep(sleep_minutes * 60)


if __name__ == "__main__":
    main()
