"""Background worker: the former `runner.py` loop, run inside the web process.

One iteration: update the user list from Nextcloud, fetch changed Collectives
pages, parse groups/protocols, then run periodic tasks (avatars, mailing
list, calendar and deck reminders). The loop respects the quiet hours and
sleep interval from the bot config.
"""

import asyncio
import logging
from datetime import datetime

import requests

from app.models import CollectivePage, Decision, Group, NCUserList, Protocol
from app.services.avatar_fetcher import AvatarFetcher
from app.services.calendar_notifier import Notifier
from app.services.collectives_loader import fetch_and_store_all_pages
from app.services.collectives_parser import (
    backfill_role_history,
    dedupe_short_names,
    parse_groups,
    parse_protocols,
    remove_stale_groups,
    sync_member_leaves,
)
from app.services.config import BotConfig
from app.services.deck_reminder import DeckReminder
from app.services.mail_fetcher import MailFetcher
from app.services.matrix_rooms import sync_default_rooms
from app.settings import settings

logger = logging.getLogger(__name__)

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
    for group in Group.fetch(limit=1000):
        group.remove()
    for p in Protocol.fetch(limit=1000):
        p.remove()
    for d in Decision.fetch(limit=1000):
        d.remove()


def get_updated_pages(update_all: bool, update_pages: str) -> list[CollectivePage]:
    """Fetch and return the list of pages to process."""
    updated_pages = fetch_and_store_all_pages()

    if update_all:
        return CollectivePage.fetch(limit=10000)

    if update_pages:
        ids = [p.strip() for p in update_pages.split(",") if p.strip()]
        return [CollectivePage.get_from_page_id(page_id=int(pid)) for pid in ids]

    return updated_pages


def process_pages(updated_pages: list[CollectivePage], force_save: bool):
    """Process updated pages: save if needed, then parse groups and protocols."""
    if force_save:
        for page in updated_pages:
            page.store()

    for page in updated_pages:
        parse_groups(page)

    # Groups whose page was deleted or archived are retired here; runs over
    # all stored groups, since archiving a page does not necessarily touch
    # its subpages' timestamps.
    remove_stale_groups()

    # Runs over all groups too: "Karenz" is a global status, so it has to be
    # reconciled against every page, not just the changed ones.
    sync_member_leaves()

    # Repairs short name lists duplicated by the old parsing; a no-op once
    # every stored group is clean.
    dedupe_short_names()

    backfill_role_history()

    for page in updated_pages:
        parse_protocols(page)


def run_periodic_tasks(userlist: NCUserList, fetcher: MailFetcher, config: BotConfig):
    """Run periodic tasks like avatar fetching, mail fetching, and notifications."""
    if config.avatare.fetch_avatar:
        AvatarFetcher(config.avatare).fetch_images(userlist)

    # The all-member chat rooms are not tied to a wiki page (unlike the group
    # rooms, which are synced in `parse_groups`), so they are reconciled once
    # per iteration — this is what picks up newly joined members.
    sync_default_rooms(userlist)

    if settings.mailinglist.imap_server:
        fetcher.fetch_maildata(userlist, config.mailer)

    Notifier(config=config.calendar_notifier).notify_upcoming_events()
    DeckReminder(config=config.deck_reminder).remind_card_due_dates()


def run_iteration(
    fetcher: MailFetcher, update_all: bool = False, update_pages: str = ""
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


async def worker_loop() -> None:
    """Run the sync/notify iteration forever inside the web process.

    The iteration itself is synchronous (requests/imaplib/caldav), so it runs
    in a thread to keep the event loop responsive.
    """
    fetcher = MailFetcher()

    while True:
        sleep_minutes = SLEEP_MINUTES_DEFAULT
        try:
            config = await asyncio.to_thread(run_iteration, fetcher)
            sleep_minutes = calculate_sleep_duration(config)
        except asyncio.CancelledError:
            raise
        except NETWORK_EXCEPTIONS as e:
            logger.warning(
                "Network error occurred, will retry after sleep: %s: %s",
                type(e).__name__,
                str(e),
            )
            sleep_minutes = await asyncio.to_thread(get_sleep_minutes_safe)
        except Exception:
            logger.exception("Worker iteration failed, will retry after sleep")

        await asyncio.sleep(sleep_minutes * 60)
