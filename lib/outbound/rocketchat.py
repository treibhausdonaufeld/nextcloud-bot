import logging

import requests

from lib.settings import settings

logger = logging.getLogger(__name__)


def send_message(text: str, channel: str) -> None:
    """Send a message to Rocket.Chat via incoming webhook."""

    webhook_url = settings.rocketchat.hook_url
    if not webhook_url:
        raise ValueError("Rocket.Chat webhook URL is not configured.")

    payload = {
        "text": text,
        "channel": channel,
        "emoji": ":robot:",
    }

    response = requests.post(str(webhook_url), json=payload)
    response.raise_for_status()
    logger.info(f"Message sent to {channel}: {text}")
