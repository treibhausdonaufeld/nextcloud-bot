import json
import logging

import requests

from lib.settings import settings

logger = logging.getLogger(__name__)


def send_message(text: str, channel: str, emoji: str = ":robot:") -> None:
    """Send a message to Rocket.Chat via incoming webhook."""

    webhook_url = settings.rocketchat.hook_url

    if settings.rocketchat.channel_overwrite:
        # for debugging porposes, override the channel
        channel = settings.rocketchat.channel_overwrite

    payload = {"text": text, "channel": channel, "emoji": emoji}

    logger.info(f"Message sent to {channel}: {text}")

    if webhook_url:
        response = requests.post(str(webhook_url), json=payload)
        # response.raise_for_status()

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
