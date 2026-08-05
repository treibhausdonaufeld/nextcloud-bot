import json
import logging
import re

import requests

from app.settings import settings

logger = logging.getLogger(__name__)


def _case_variants(channel: str) -> list[str]:
    """Build a list of channel-name casings to try, in order.

    Rocket.Chat channel names are case-sensitive, so a channel written as
    ``AG-Haus`` in the bot configuration page will not be found under
    ``ag-haus`` or ``AG-HAUS``. The first candidate is always the channel
    exactly as configured; further candidates cover common alternate
    casings (all lower, all upper, title-cased segments, and an
    upper-prefix/lower-rest form like group prefixes such as ``AG-haus``)
    so a send still succeeds if the channel actually exists under a
    different casing than what was configured.
    """
    variants: list[str] = []

    def add(candidate: str) -> None:
        if candidate and candidate not in variants:
            variants.append(candidate)

    add(channel)
    add(channel.lower())
    add(channel.upper())
    add(re.sub(r"[^-_\s]+", lambda m: m.group(0).capitalize(), channel))

    parts = re.split(r"(-)", channel)
    if len(parts) > 1:
        upper_prefix_rest_lower = parts[0].upper() + "".join(
            part if part == "-" else part.lower() for part in parts[1:]
        )
        add(upper_prefix_rest_lower)

    return variants


def send_rocketchat_message(text: str, channel: str, emoji: str = ":robot:") -> None:
    """Send a message to Rocket.Chat via incoming webhook.

    Uses the channel name exactly as written in the bot configuration page
    first. If Rocket.Chat rejects that (case-sensitive channel names), other
    common casings are tried before giving up.
    """

    webhook_url = settings.rocketchat.hook_url

    if settings.rocketchat.channel_overwrite:
        # for debugging purposes, override the channel
        channel = settings.rocketchat.channel_overwrite

    if not webhook_url:
        logger.warning(
            "Chat URL not configured, this is the message: %s",
            json.dumps({"text": text, "channel": channel, "emoji": emoji}),
        )
        return

    # Direct messages (``@user``) aren't affected by channel-casing issues.
    candidates = [channel] if channel.startswith("@") else _case_variants(channel)

    last_response: requests.Response | None = None
    for candidate in candidates:
        payload = {"text": text, "channel": candidate, "emoji": emoji}
        try:
            response = requests.post(str(webhook_url), json=payload, timeout=90)
        except requests.RequestException:
            logger.exception(
                "Request to Rocket.Chat webhook failed for channel %r", candidate
            )
            last_response = None
            continue

        if response.status_code == 200:
            if candidate != channel:
                logger.warning(
                    "Sent notification using alternate channel casing %r "
                    "(configured casing %r failed)",
                    candidate,
                    channel,
                )
            else:
                logger.debug(
                    "Sent notification to channel %s: %s",
                    candidate,
                    json.dumps(payload),
                )
            return

        last_response = response
        logger.debug(
            "Attempt to send to channel %r failed (%s): %s",
            candidate,
            response.status_code,
            response.text,
        )

    logger.error(
        "Failed to send notification to channel %s after trying %d casing(s) (%s): %s",
        channel,
        len(candidates),
        ", ".join(candidates),
        last_response.text if last_response is not None else "",
    )


# Backwards-compatible alias. Generic notifications should go through
# ``app.services.notify.send_message`` which dispatches via Apprise and falls
# back to this Rocket.Chat webhook.
send_message = send_rocketchat_message
