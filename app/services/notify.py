"""Generic notification dispatch via the Apprise library.

Notifications are addressed by a logical *channel* name (e.g. ``ug-it`` or
``@username``). The bot config (:class:`app.services.config.NotifierConfig`)
maps each channel to one or more Apprise service URLs, which lets the bot
deliver to any service Apprise supports (Matrix, Telegram, Discord, e-mail,
Rocket.Chat, ...).

When a channel has no Apprise targets configured, the message falls back to
the legacy Rocket.Chat incoming webhook so existing deployments keep working
without any config change.
"""

import logging

import apprise

from app.services.config import bot_config
from app.services.rocketchat import send_rocketchat_message
from app.settings import settings

logger = logging.getLogger(__name__)


def _resolve_urls(channel: str) -> list[str]:
    """Collect the Apprise URLs configured for ``channel``.

    Combines the always-on ``default_urls`` with any channel-specific targets.
    Returns an empty list when the notifier is disabled or nothing matches.
    """
    notifier = bot_config.notifier
    if not notifier.enabled:
        return []

    urls: list[str] = list(notifier.default_urls)
    urls.extend(notifier.channels.get(channel, []))
    return urls


def send_message(text: str, channel: str, emoji: str = ":robot:") -> None:
    """Send a notification to ``channel``.

    Routes the message to any Apprise targets configured for the channel. If
    none are configured, falls back to the legacy Rocket.Chat webhook (which
    routes by channel name in the payload).
    """

    notifier = bot_config.notifier
    if notifier.channel_overwrite:
        # for debugging purposes, override the channel
        channel = notifier.channel_overwrite

    urls = _resolve_urls(channel)

    if not urls:
        # No generic targets configured -> keep the legacy Rocket.Chat behaviour.
        send_rocketchat_message(text=text, channel=channel, emoji=emoji)
        return

    apobj = apprise.Apprise()
    added = 0
    for url in urls:
        if apobj.add(url):
            added += 1
        else:
            logger.error("Invalid Apprise URL for channel %s: %s", channel, url)

    if not added:
        logger.warning("No valid Apprise targets for channel %s", channel)
        return

    title = notifier.title or settings.name

    if apobj.notify(body=text, title=title):
        logger.info(
            "Sent notification to channel %s via %d Apprise target(s)", channel, added
        )
    else:
        logger.error("Failed to send Apprise notification to channel %s", channel)
