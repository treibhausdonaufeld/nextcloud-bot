import logging
import time
from datetime import datetime

import click

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
from lib.settings import settings

logger = logging.getLogger(__name__)

# reduce logging for httpx package to WARNING
logging.getLogger("httpx").setLevel(logging.WARNING)


def delete_all_parsed_data():
    for group in Group.get_all():
        group.delete()
    for p in Protocol.get_all():
        p.delete()
    for d in Decision.get_all():
        d.delete()


@click.command()
@click.option("--loop", is_flag=True, default=False, help="Run main() in a loop")
@click.option(
    "--update-all", is_flag=True, default=False, help="Update/re-parse all pages"
)
def main(loop: bool, update_all: bool):
    logger.debug("Starting runner")

    fetcher = MailFetcher()

    while True:
        # Update all users from Nextcloud
        userlist = NCUserList()
        userlist.update_from_nextcloud()

        updated_pages = fetch_and_store_all_pages()
        if update_all:
            updated_pages = CollectivePage.get_all(limit=1000)

        for page in updated_pages:
            parse_groups(page)

        for page in updated_pages:
            parse_protocols(page)

        config = BotConfig.load_config()

        nc_users = NCUserList()

        if config.avatare.fetch_avatar:
            AvatarFetcher(config.avatare).fetch_images(nc_users)

        if settings.mailinglist.imap_server:
            fetcher.fetch_maildata(nc_users, config.mailer)

        if (8 < datetime.now().hour < 20) and config.calendar_notifier.enabled:
            Notifier(config=config.calendar_notifier).notify_upcoming_events()

        if (8 < datetime.now().hour < 20) and config.deck_reminder.enabled:
            DeckReminder(config=config.deck_reminder).remind_card_due_dates()

        if not loop:
            break

        time.sleep(config.sleep_minutes * 60)


if __name__ == "__main__":
    main()
