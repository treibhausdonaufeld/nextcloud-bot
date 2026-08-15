"""Generic notification dispatch via the Apprise library.

Notifications are addressed by a logical *channel* name (e.g. ``ug-it`` or
``@username``). The bot config (:class:`app.services.config.NotifierConfig`)
maps each channel to one or more Apprise service URLs, which lets the bot
deliver to any service Apprise supports (Matrix, Telegram, Discord, e-mail,
Rocket.Chat, ...).

When a channel has no Apprise targets configured, the message is delivered
into the channel's Matrix room if one exists (see
:mod:`app.services.matrix_notify`) and to the legacy Rocket.Chat incoming
webhook, so existing deployments keep working without any config change.
While both chat systems are configured every notification goes to both
(``NOTIFY_DUAL_SEND``); otherwise Rocket.Chat only receives what Matrix
could not deliver.
"""

import logging

import apprise

from app.services.config import bot_config
from app.services.matrix import matrix_enabled
from app.services.matrix_notify import send_matrix_message
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


def dual_send_enabled() -> bool:
    """Whether a notification goes to Matrix *and* Rocket.Chat.

    True while both chat systems are configured (and `NOTIFY_DUAL_SEND` is
    not switched off), which is what a migration period wants: the message
    reaches the people who already moved to Matrix as well as those still on
    Rocket.Chat. Otherwise Rocket.Chat stays what it was — the fallback for
    whatever Matrix could not deliver.
    """
    return bool(
        settings.notify_dual_send and matrix_enabled() and settings.rocketchat.hook_url
    )


def target_channel(channel: str) -> str:
    """Apply the testing overrides to a channel name.

    ``NOTIFY_CHANNEL_OVERWRITE`` (env) wins over the bot-config page's
    ``notifier.channel_overwrite``, so a deployment can be tested without
    having to edit the configuration page — see `app.settings.Settings`.
    """
    if settings.notify_channel_overwrite:
        return settings.notify_channel_overwrite

    try:
        return bot_config.notifier.channel_overwrite or channel
    except Exception:  # bot config page unavailable
        return channel


def send_message(text: str, channel: str, emoji: str = ":robot:") -> None:
    """Send a notification to ``channel``.

    Routes the message to any Apprise targets configured for the channel. If
    none are configured, it goes to the channel's Matrix room and to the
    legacy Rocket.Chat webhook (which routes by channel name in the payload)
    — to both while both are configured, otherwise to whichever can deliver.
    """

    notifier = bot_config.notifier
    channel = target_channel(channel)

    urls = _resolve_urls(channel)

    if not urls:
        # No explicit Apprise target: try the channel's Matrix room (every
        # group has one, see `app.services.matrix_rooms`). Rocket.Chat then
        # either gets a copy of every message (dual send, while both systems
        # are configured) or only what Matrix could not deliver.
        delivered = send_matrix_message(text=text, channel=channel)

        if not delivered or dual_send_enabled():
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
