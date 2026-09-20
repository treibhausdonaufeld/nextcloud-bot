#!/usr/bin/env python3
"""Apply the mailbox desired state published by the nextcloud-bot.

The bot container is deliberately unprivileged, so it only writes the file
<mailbox_state_file> (default inside the container: /data/mailboxes.json; on
the host usually /mnt/thd-data/log/data/mailboxes.json). This script runs on
the Docker host (systemd timer, cron or Ansible) and performs the privileged
work by driving the existing IaC scripts:

  * <mailserver-dir>/mailbox_ctl.sh     create-shared / share-add
  * <nextcloud-dir>/mailbox_configure.sh  --update | create

For every configured mailbox it ensures the shared docker-mailserver account
exists and grants the listed users access (ACL + subscription), and for every
user it ensures the matching account in the Nextcloud Mail app. It is
idempotent: existing mailboxes/accounts are left alone and only updated.

Typical systemd unit:

    [Service]
    Type=oneshot
    Environment=MAILSERVER_DIR=/home/thd/iac/services/mailserver
    Environment=NEXTCLOUD_DIR=/home/thd/iac/services/nextcloud
    ExecStart=/usr/bin/python3 /home/thd/nextcloud-bot/scripts/mailbox_provisioner.py

Environment / options (CLI wins):
  MAILBOX_STATE_FILE      desired state (default: /mnt/thd-data/log/data/mailboxes.json)
  MAILBOX_STATUS_FILE     result file   (default: <state dir>/mailboxes.status.json)
  MAILBOX_PASSWORDS_FILE  new passwords (default: <state dir>/mailboxes.passwords.txt, 0600)
  MAILSERVER_DIR          dir with mailbox_ctl.sh
  NEXTCLOUD_DIR           dir with mailbox_configure.sh (+ docker compose)
  MAIL_SERVER             IMAP/SMTP host for Nextcloud Mail
  MAIL_DOMAIN             mailserver domain, for local parts
  ACCOUNTS_CSV            mailserver password CSV

Only the standard library is used, so the script can run on a plain host.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STATE_FILE = "/mnt/thd-data/log/data/mailboxes.json"
DEFAULT_MAIL_DOMAIN = "treibhausdonaufeld.at"
CREDENTIAL_RE = re.compile(r"^(?P<address>\S+@\S+)\t(?P<password>\S+)\s*$")
# `doveadm acl get` entries look like "... user=name@domain lookup read ...".
USER_RE = re.compile(r"user=([^\s]+)")


def log(message: str) -> None:
    print(message, flush=True)


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr, flush=True)


def run(command: list[str], cwd: Path, env: dict, dry_run: bool, timeout: int):
    """Run a provisioner command, returning (ok, returncode, stdout, stderr)."""
    pretty = " ".join(command)
    if dry_run:
        log(f"  [dry-run] (cd {cwd}) {pretty}")
        return True, 0, "", ""

    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, -1, "", str(exc)

    if completed.returncode != 0 and completed.stderr.strip():
        for line in completed.stderr.strip().splitlines():
            log(f"    ! {line}")
    return (
        completed.returncode == 0,
        completed.returncode,
        completed.stdout,
        completed.stderr,
    )


def to_email(name: str, mail_domain: str) -> str:
    """Mirror mailbox_ctl.sh's to_email(): append the domain to a local part."""
    return name if "@" in name else f"{name}@{mail_domain}"


def current_acl(
    address: str,
    ctl: Path,
    cwd: Path,
    env: dict,
    dry_run: bool,
    timeout: int,
) -> set[str] | None:
    """Return the mail addresses currently granted access, or None on error."""
    ok, _, stdout, _ = run(
        [str(ctl), "share-list", address], cwd, env, dry_run, timeout
    )
    if not ok:
        return None
    return {match.group(1).lower() for match in USER_RE.finditer(stdout)}


def ensure_shared_mailbox(
    item: dict,
    ctl: Path,
    cwd: Path,
    env: dict,
    dry_run: bool,
    timeout: int,
    mail_domain: str,
) -> tuple[bool, str, list[str]]:
    """Ensure the shared mailbox and ACLs.

    Returns (ok, captured stdout, revoked addresses). Access that is no longer
    in the desired users is revoked, so removing a user from the config also
    removes it on the mailserver.
    """
    address = item["address"]
    # Use the internal username (authentik handle, e.g. "fabian.helm"), not the
    # user's mail address: mailbox_ctl.sh appends the mail domain itself, so a
    # foreign/alias address would create the wrong personal mailbox.
    logins = [
        user["handle"] or user["email"]
        for user in item.get("users", [])
        if user.get("handle") or user.get("email")
    ]

    ok, _, created_out, _ = run(
        [str(ctl), "create-shared", address], cwd, env, dry_run, timeout
    )
    if not ok:
        return False, created_out, []

    if not logins:
        return True, created_out, []

    ok, _, share_out, _ = run(
        [str(ctl), "share-add", address, *logins], cwd, env, dry_run, timeout
    )
    output = created_out + share_out
    if not ok:
        return False, output, []

    # Never revoke while a configured user is unresolved: their entry is
    # missing from `logins`, so reconciliation would drop a valid grantee.
    if item.get("unresolved"):
        log("  not revoking access: unresolved users present")
        return True, output, []

    acl = current_acl(address, ctl, cwd, env, dry_run, timeout)
    if acl is None:
        log("  ! could not read current access, skipping revocation")
        return False, output, []

    # The mailbox itself is never a grantee to revoke.
    acl.discard(address.lower())

    desired = {to_email(login, mail_domain).lower() for login in logins}
    revoked: list[str] = []
    for address_to_revoke in sorted(acl - desired):
        ok, _, revoke_out, _ = run(
            [str(ctl), "share-remove", address, address_to_revoke],
            cwd,
            env,
            dry_run,
            timeout,
        )
        if not ok:
            return False, output, revoked
        output += revoke_out
        revoked.append(address_to_revoke)
        log(f"  revoked access for {address_to_revoke}")

    return True, output, revoked


def prune_nextcloud_accounts(
    item: dict,
    address: str,
    configure: Path,
    cwd: Path,
    env: dict,
    dry_run: bool,
    timeout: int,
) -> bool:
    """Delete Mail accounts of users no longer in the config for this mailbox."""
    if item.get("unresolved"):
        # An unresolved entry would look like a removed user: do not prune.
        log("  not pruning Mail accounts: unresolved users present")
        return True

    uids = [user["username"] for user in item.get("users", [])]
    if not uids:
        # No usable uid list: never mass-delete accounts for the mailbox.
        log("  not pruning Mail accounts: no resolved users")
        return True

    ok, *_ = run(
        [str(configure), "--prune", address, *uids], cwd, env, dry_run, timeout
    )
    return ok


def ensure_nextcloud_account(
    user: dict,
    address: str,
    configure: Path,
    cwd: Path,
    env: dict,
    dry_run: bool,
    timeout: int,
) -> tuple[bool, str]:
    """Ensure the user's Nextcloud Mail account exists; return (ok, stdout).

    `mailbox_configure.sh` creates an account only when it is missing, so an
    account that is already configured is left untouched (no update pass).
    """
    uid = user["username"]
    ok, _, stdout, _ = run([str(configure), uid, address], cwd, env, dry_run, timeout)
    return ok, stdout


def collect_credentials(text: str, collected: list[tuple[str, str]]) -> None:
    """Collect "<address>\\t<password>" lines printed by mailbox_ctl.sh."""
    for line in text.splitlines():
        match = CREDENTIAL_RE.match(line)
        if match:
            collected.append((match.group("address"), match.group("password")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--state-file",
        default=os.environ.get("MAILBOX_STATE_FILE", DEFAULT_STATE_FILE),
        help="desired-state file written by the bot",
    )
    parser.add_argument(
        "--status-file",
        default=os.environ.get("MAILBOX_STATUS_FILE", ""),
        help="where to write the result (default: next to the state file)",
    )
    parser.add_argument(
        "--passwords-file",
        default=os.environ.get("MAILBOX_PASSWORDS_FILE", ""),
        help="where to record newly generated passwords (default: next to the state file)",
    )
    parser.add_argument(
        "--mailserver-dir",
        default=os.environ.get("MAILSERVER_DIR", ""),
        help="directory containing mailbox_ctl.sh",
    )
    parser.add_argument(
        "--nextcloud-dir",
        default=os.environ.get("NEXTCLOUD_DIR", ""),
        help="directory containing mailbox_configure.sh and the docker compose stack",
    )
    parser.add_argument("--mail-server", default=os.environ.get("MAIL_SERVER", ""))
    parser.add_argument("--mail-domain", default=os.environ.get("MAIL_DOMAIN", ""))
    parser.add_argument("--accounts-csv", default=os.environ.get("ACCOUNTS_CSV", ""))
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="ADDRESS",
        help="only apply this mailbox (repeatable)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(argv)

    state_file = Path(args.state_file)
    status_file = (
        Path(args.status_file)
        if args.status_file
        else state_file.with_name("mailboxes.status.json")
    )
    passwords_file = (
        Path(args.passwords_file)
        if args.passwords_file
        else state_file.with_name("mailboxes.passwords.txt")
    )

    if not state_file.is_file():
        fail(f"state file not found: {state_file}")
        return 1

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read state file {state_file}: {exc}")
        return 1

    if not args.mailserver_dir or not args.nextcloud_dir:
        fail(
            "--mailserver-dir and --nextcloud-dir (or the matching env vars) are required"
        )
        return 1

    mailserver_dir = Path(args.mailserver_dir)
    nextcloud_dir = Path(args.nextcloud_dir)
    ctl = mailserver_dir / "mailbox_ctl.sh"
    configure = nextcloud_dir / "mailbox_configure.sh"
    for path in (ctl, configure):
        if not path.is_file():
            fail(f"script not found: {path}")
            return 1

    env = os.environ.copy()
    if args.mail_server:
        env["MAIL_SERVER"] = args.mail_server
    if args.mail_domain:
        env["MAIL_DOMAIN"] = args.mail_domain
    if args.accounts_csv:
        env["ACCOUNTS_CSV"] = args.accounts_csv
    # Must match the domain mailbox_ctl.sh uses; without an explicit
    # --mail-domain both fall back to the same default.
    mail_domain = args.mail_domain or DEFAULT_MAIL_DOMAIN

    selected = set(address.lower() for address in args.only)
    mailboxes = [
        item
        for item in state.get("mailboxes", [])
        if not selected or item["address"].lower() in selected
    ]

    log(
        f"Applying {len(mailboxes)} mailbox(es) from {state_file}"
        + (" [dry-run]" if args.dry_run else "")
    )

    results = []
    credentials: list[tuple[str, str]] = []
    failed = 0

    for item in mailboxes:
        address = item["address"]
        log(f"- {address} ({item.get('name', '')})")

        if item.get("unresolved"):
            log(f"  unresolved users skipped: {', '.join(item['unresolved'])}")

        ok, shared_out, revoked = ensure_shared_mailbox(
            item, ctl, mailserver_dir, env, args.dry_run, args.timeout, mail_domain
        )
        collect_credentials(shared_out, credentials)

        user_results = []
        for user in item.get("users", []):
            user_ok, stdout = ensure_nextcloud_account(
                user, address, configure, nextcloud_dir, env, args.dry_run, args.timeout
            )
            collect_credentials(stdout, credentials)
            user_results.append({"username": user["username"], "ok": user_ok})
            if not user_ok:
                ok = False
                log(f"  ! Nextcloud Mail account failed for {user['username']}")

        if ok and not prune_nextcloud_accounts(
            item, address, configure, nextcloud_dir, env, args.dry_run, args.timeout
        ):
            ok = False
            log("  ! pruning Nextcloud Mail accounts failed")

        if not ok:
            failed += 1
        results.append(
            {
                "address": address,
                "ok": ok,
                "users": user_results,
                "unresolved": item.get("unresolved", []),
                "revoked": revoked,
            }
        )

    if credentials and not args.dry_run:
        try:
            with passwords_file.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"# {datetime.now(timezone.utc).isoformat()} by mailbox_provisioner\n"
                )
                for address, password in credentials:
                    handle.write(f"{address}\t{password}\n")
            passwords_file.chmod(0o600)
            log(f"recorded {len(credentials)} new credential(s) in {passwords_file}")
        except OSError as exc:
            log(f"warning: could not record credentials: {exc}")

    status = {
        "applied_at": int(datetime.now(timezone.utc).timestamp()),
        "dry_run": args.dry_run,
        "mailboxes": results,
        "failed": failed,
    }
    try:
        status_file.write_text(
            json.dumps(status, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        log(f"warning: could not write status file {status_file}: {exc}")

    log(f"done: {len(mailboxes) - failed}/{len(mailboxes)} mailbox(es) applied")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
