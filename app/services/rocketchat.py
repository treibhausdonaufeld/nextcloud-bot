import json
import logging

import requests

from app.settings import settings

logger = logging.getLogger(__name__)


def send_rocketchat_message(text: str, channel: str, emoji: str = ":robot:") -> None:
    """Send a message to Rocket.Chat via incoming webhook."""

    webhook_url = settings.rocketchat.hook_url

    if settings.rocketchat.channel_overwrite:
        # for debugging porposes, override the channel
        channel = settings.rocketchat.channel_overwrite

    payload = {"text": text, "channel": channel, "emoji": emoji}

    logger.info(f"Message sent to {channel}: {text}")

    if webhook_url:
        response = requests.post(str(webhook_url), json=payload)

        # log error if request failed
        if response.status_code != 200:
            logger.error(
                "Failed to send notification to channel %s: %s",
                channel,
                response.text,
            )
        else:
            logger.debug(
                "Sent notification to channel %s: %s", channel, json.dumps(payload)
            )
    else:
        logger.warning(
            "Chat URL not configured, this is the message: %s", json.dumps(payload)
        )


# Backwards-compatible alias. Generic notifications should go through
# ``app.services.notify.send_message`` which dispatches via Apprise and falls
# back to this Rocket.Chat webhook.
send_message = send_rocketchat_message
