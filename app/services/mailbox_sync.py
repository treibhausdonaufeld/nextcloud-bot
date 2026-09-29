"""Publish the desired mailbox state for the host-side provisioner.

The bot runs in a hardened container and cannot execute the Nextcloud ``occ``
commands or the docker-mailserver setup tools that mailbox provisioning needs.
It therefore only *describes* the mailboxes configured on the bot-config page:
each worker iteration writes ``mailboxes.json`` (see ``mailbox_state_file``),
and the host-side applier ``scripts/mailbox_provisioner.py`` reads it and does
the privileged work with the existing IaC scripts. The file is only rewritten
when its content changes, so a host timer can poll it cheaply.

As a side effect the authentik mail group (``AUTH__MAIL_GROUP_NAME``) is kept
in sync with the mailbox users — that group is what grants access to the mail
app, so everybody with a shared mailbox is added and whoever lost their last
mailbox is removed again; see ``app.services.authentik``.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.models.user import NCUserList
from app.services.authentik import sync_members
from app.services.config import BotConfig
from app.settings import settings

logger = logging.getLogger(__name__)

STATE_VERSION = 1


def build_state(nc_users: NCUserList, config: BotConfig) -> dict:
    """Build the serializable desired state of all configured mailboxes."""
    mailboxes = []
    unresolved: list[str] = []

    for address, item in sorted(config.mailbox.items()):
        users = []
        seen: set[str] = set()
        mailbox_unresolved = []

        for identifier in item.users:
            user = nc_users.resolve_user(identifier)
            if user is None:
                mailbox_unresolved.append(identifier)
                unresolved.append(identifier)
                continue
            if user.username in seen:
                continue
            seen.add(user.username)
            users.append(
                {
                    "username": user.username,
                    "handle": user.authentik_username or user.username,
                    "email": user.email,
                }
            )

        mailboxes.append(
            {
                "address": address.lower(),
                "name": item.name or address,
                "users": users,
                "unresolved": mailbox_unresolved,
            }
        )

    return {
        "version": STATE_VERSION,
        "generated_at": int(datetime.now(timezone.utc).timestamp()),
        "mailboxes": mailboxes,
        "unresolved": unresolved,
    }


def mail_group_members(state: dict) -> list[str]:
    """Nextcloud uids (authentik uuids) of everybody with a shared mailbox."""
    return sorted(
        {
            user["username"]
            for mailbox in state["mailboxes"]
            for user in mailbox["users"]
        }
    )


def sync_mailboxes(nc_users: NCUserList, config: BotConfig) -> None:
    """Write the desired mailbox state to disk if it changed.

    An empty ``mailbox`` config leaves an existing file alone: the applier
    never deletes mailboxes (that is destructive), so wiping the file would
    only stop future updates without cleaning anything up.
    """
    if not config.mailbox:
        logger.debug("No mailboxes configured, keeping existing state file")
        return

    state = build_state(nc_users, config)
    if state["unresolved"]:
        logger.warning(
            "Mailbox users that could not be resolved: %s",
            ", ".join(state["unresolved"]),
        )

    # Mirror the mail group onto the mailbox users: everybody with a shared
    # mailbox gets mail app access, and whoever lost their last mailbox loses
    # it again. Removals are skipped while a config entry is unresolved, so a
    # typo cannot strip access from everyone else.
    sync_members(
        settings.auth.mail_group_name,
        mail_group_members(state),
        remove_missing=not state["unresolved"],
    )

    path = Path(settings.mailbox_state_file)
    serialized = json.dumps(state, indent=2, ensure_ascii=False) + "\n"

    try:
        if path.exists() and path.read_text(encoding="utf-8") == serialized:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(serialized, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.exception("Failed to write mailbox state file %s", path)
        return

    logger.info(
        "Published desired state for %d mailboxes to %s",
        len(state["mailboxes"]),
        path,
    )
