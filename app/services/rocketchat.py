import json
import logging
import re

import requests

from app.settings import settings

logger = logging.getLogger(__name__)


def _case_variants(channel: str) -> list[str]:
    """Build a list of channel-name casings to try, in order.

    Rocket.Chat channel names are case-sensitive, so a channel written as
    ``ag-struktur`` in the bot configuration page will not be found under a
    differently-cased channel. Group channels here follow an
    uppercase-prefix convention with either a title-cased rest for ordinary
    words (e.g. ``AG-Struktur``) or a fully-uppercase rest for acronyms
    (e.g. ``UG-IT``) -- there's no reliable way to tell which a given
    lowercase segment is, so both forms are tried first since they're the
    casings the channel is actually likely to be created under; the channel
    exactly as configured is tried next, followed by other common alternate
    casings (all lower, title-cased segments including the prefix, and an
    upper-prefix/lower-rest form) so a send still succeeds if the channel
    exists under yet another casing.
    """
    variants: list[str] = []

    def add(candidate: str) -> None:
        if candidate and candidate not in variants:
            variants.append(candidate)

    parts = re.split(r"(-)", channel)
    if len(parts) > 1:
        upper_prefix_rest_title = parts[0].upper() + "".join(
            part if part == "-" else part.capitalize() for part in parts[1:]
        )
        add(upper_prefix_rest_title)
        # Acronym suffixes (e.g. "IT") don't survive title-casing, so also
        # try the fully-uppercase form early for prefixed channels.
        add(channel.upper())

    add(channel)
    add(channel.lower())
    add(channel.upper())
    add(re.sub(r"[^-_\s]+", lambda m: m.group(0).capitalize(), channel))

    if len(parts) > 1:
        upper_prefix_rest_lower = parts[0].upper() + "".join(
            part if part == "-" else part.lower() for part in parts[1:]
        )
        add(upper_prefix_rest_lower)

    return variants


def send_rocketchat_message(text: str, channel: str, emoji: str = ":robot:") -> None:
    """Send a message to Rocket.Chat via incoming webhook.

    Tries the group-channel casing convention (uppercase prefix, title-cased
    rest, e.g. ``AG-Struktur``) first, then the channel exactly as written in
    the bot configuration page, then other common alternate casings, since
    Rocket.Chat channel names are case-sensitive.
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
