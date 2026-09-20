"""Tests for mailbox desired-state publishing and the host-side applier."""

import importlib.util
import json
from pathlib import Path

from app.models.user import NCUser, NCUserList
from app.services.config import BotConfig, MailboxItem
from app.services.mailbox_sync import build_state, sync_mailboxes
from app.settings import settings

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def load_provisioner():
    spec = importlib.util.spec_from_file_location(
        "mailbox_provisioner", SCRIPTS_DIR / "mailbox_provisioner.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_users(*users: NCUser) -> NCUserList:
    userlist = NCUserList.__new__(NCUserList)
    userlist.users = {user.username: user for user in users}
    return userlist


def sample_users() -> NCUserList:
    return make_users(
        NCUser(
            username="uuid-1",
            email="fabian.helm@treibhausdonaufeld.at",
            displayname="Fabian Helm",
            authentik_username="fabian.helm",
        ),
        NCUser(
            username="uuid-2",
            email="gabi@treibhausdonaufeld.at",
            displayname="Gabriele Adebisi-Schuster",
            authentik_username="gabriele.adebisi-schuster",
        ),
    )


def sample_config(users=("fabian.helm", "gabriele.adebisi-schuster")) -> BotConfig:
    return BotConfig(
        mailbox={
            "office@treibhausdonaufeld.at": MailboxItem(
                name="Treibhaus Donaufeld", users=list(users)
            )
        }
    )


def test_mailbox_config_is_parsed():
    config = sample_config()
    item = config.mailbox["office@treibhausdonaufeld.at"]
    assert item.name == "Treibhaus Donaufeld"
    assert item.users == ["fabian.helm", "gabriele.adebisi-schuster"]


def test_resolve_user_by_all_identifiers():
    users = sample_users()
    assert users.resolve_user("uuid-1").username == "uuid-1"
    assert users.resolve_user("fabian.helm").username == "uuid-1"
    assert users.resolve_user("Fabian Helm").username == "uuid-1"
    assert users.resolve_user("fabian.helm@treibhausdonaufeld.at").username == "uuid-1"
    assert users.resolve_user("nobody") is None


def test_resolve_user_ambiguous_display_name_returns_none():
    users = make_users(
        NCUser(username="a", displayname="Alex"),
        NCUser(username="b", displayname="Alex"),
    )
    assert users.resolve_user("Alex") is None


def test_build_state_resolves_users_and_reports_unresolved():
    config = sample_config(users=["fabian.helm", "ghost"])
    state = build_state(sample_users(), config)

    assert state["unresolved"] == ["ghost"]
    mailbox = state["mailboxes"][0]
    assert mailbox["address"] == "office@treibhausdonaufeld.at"
    assert mailbox["unresolved"] == ["ghost"]
    assert [u["handle"] for u in mailbox["users"]] == ["fabian.helm"]
    assert mailbox["users"][0]["email"] == "fabian.helm@treibhausdonaufeld.at"


def test_sync_mailboxes_writes_file(tmp_path, monkeypatch):
    path = tmp_path / "mailboxes.json"
    monkeypatch.setattr(settings, "mailbox_state_file", str(path))

    sync_mailboxes(sample_users(), sample_config())

    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["version"] == 1
    assert len(state["mailboxes"]) == 1
    assert not (tmp_path / "mailboxes.json.tmp").exists()


def test_sync_mailboxes_leaves_file_alone_when_unconfigured(tmp_path, monkeypatch):
    path = tmp_path / "mailboxes.json"
    path.write_text("untouched", encoding="utf-8")
    monkeypatch.setattr(settings, "mailbox_state_file", str(path))

    sync_mailboxes(sample_users(), BotConfig())

    assert path.read_text(encoding="utf-8") == "untouched"


def write_fake_scripts(tmp_path: Path) -> tuple[Path, Path]:
    mailserver_dir = tmp_path / "mailserver"
    nextcloud_dir = tmp_path / "nextcloud"
    mailserver_dir.mkdir()
    nextcloud_dir.mkdir()

    args_log = tmp_path / "ctl_args.log"
    acl_file = tmp_path / "ctl_acl.txt"
    acl_file.write_text("", encoding="utf-8")
    ctl = mailserver_dir / "mailbox_ctl.sh"
    ctl.write_text(
        "#!/bin/sh\n"
        'cmd="$1"; shift\n'
        f'log="{args_log}"\n'
        f'acl="{acl_file}"\n'
        'case "$cmd" in\n'
        '  create-shared) echo "create-shared $*" >> "$log"; echo "created $1" ;;\n'
        '  share-list) cat "$acl" 2>/dev/null ;;\n'
        "  share-add)\n"
        '    echo "share-add $*" >> "$log"\n'
        '    for login in "$@"; do\n'
        '      printf "%s\\tpw-%s\\n" "$login@treibhausdonaufeld.at" "$login"\n'
        "    done\n"
        "    ;;\n"
        '  share-remove) echo "share-remove $*" >> "$log" ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    ctl.chmod(0o755)

    configure = nextcloud_dir / "mailbox_configure.sh"
    configure.write_text(
        f'#!/bin/sh\necho "$*" >> "{tmp_path / "configure_args.log"}"\nexit 0\n',
        encoding="utf-8",
    )
    configure.chmod(0o755)
    return mailserver_dir, nextcloud_dir


def test_provisioner_dry_run_does_not_execute(tmp_path):
    provisioner = load_provisioner()
    state_file = tmp_path / "mailboxes.json"
    state_file.write_text(
        json.dumps(build_state(sample_users(), sample_config())), encoding="utf-8"
    )
    mailserver_dir, nextcloud_dir = write_fake_scripts(tmp_path)

    rc = provisioner.main(
        [
            "--state-file",
            str(state_file),
            "--mailserver-dir",
            str(mailserver_dir),
            "--nextcloud-dir",
            str(nextcloud_dir),
            "--dry-run",
        ]
    )

    assert rc == 0
    status = json.loads((tmp_path / "mailboxes.status.json").read_text())
    assert status["dry_run"] is True
    assert status["failed"] == 0
    assert not (tmp_path / "mailboxes.passwords.txt").exists()


def test_provisioner_applies_revokes_and_records_credentials(tmp_path):
    provisioner = load_provisioner()
    state_file = tmp_path / "mailboxes.json"
    state_file.write_text(
        json.dumps(build_state(sample_users(), sample_config())), encoding="utf-8"
    )
    mailserver_dir, nextcloud_dir = write_fake_scripts(tmp_path)
    (tmp_path / "ctl_acl.txt").write_text(
        "user=fabian.helm@treibhausdonaufeld.at lookup read write\n"
        "user=gabriele.adebisi-schuster@treibhausdonaufeld.at lookup read\n"
        "user=exmember@treibhausdonaufeld.at lookup read write\n",
        encoding="utf-8",
    )

    rc = provisioner.main(
        [
            "--state-file",
            str(state_file),
            "--mailserver-dir",
            str(mailserver_dir),
            "--nextcloud-dir",
            str(nextcloud_dir),
        ]
    )

    assert rc == 0
    status = json.loads((tmp_path / "mailboxes.status.json").read_text())
    assert status["failed"] == 0
    assert status["mailboxes"][0]["ok"] is True
    assert all(user["ok"] for user in status["mailboxes"][0]["users"])
    assert status["mailboxes"][0]["revoked"] == ["exmember@treibhausdonaufeld.at"]

    ctl_args = (tmp_path / "ctl_args.log").read_text().splitlines()
    assert [line for line in ctl_args if line.startswith("share-add")] == [
        "share-add office@treibhausdonaufeld.at fabian.helm gabriele.adebisi-schuster"
    ]
    # A user that is no longer configured loses access again.
    assert [line for line in ctl_args if line.startswith("share-remove")] == [
        "share-remove office@treibhausdonaufeld.at exmember@treibhausdonaufeld.at"
    ]

    configure_calls = (tmp_path / "configure_args.log").read_text().splitlines()
    assert configure_calls == [
        "uuid-1 office@treibhausdonaufeld.at",
        "uuid-2 office@treibhausdonaufeld.at",
        "--prune office@treibhausdonaufeld.at uuid-1 uuid-2",
    ]
    # Accounts are only created when missing, never updated.
    assert not any("--update" in call for call in configure_calls)

    passwords = (tmp_path / "mailboxes.passwords.txt").read_text()
    assert "fabian.helm@treibhausdonaufeld.at\tpw-fabian.helm" in passwords
    assert (
        "gabriele.adebisi-schuster@treibhausdonaufeld.at\tpw-gabriele.adebisi-schuster"
        in passwords
    )
    assert (tmp_path / "mailboxes.passwords.txt").stat().st_mode & 0o777 == 0o600


def test_provisioner_does_not_revoke_when_a_user_is_unresolved(tmp_path):
    provisioner = load_provisioner()
    state_file = tmp_path / "mailboxes.json"
    state_file.write_text(
        json.dumps(
            build_state(sample_users(), sample_config(users=["fabian.helm", "ghost"]))
        ),
        encoding="utf-8",
    )
    mailserver_dir, nextcloud_dir = write_fake_scripts(tmp_path)
    (tmp_path / "ctl_acl.txt").write_text(
        "user=fabian.helm@treibhausdonaufeld.at lookup read write\n"
        "user=gabriele.adebisi-schuster@treibhausdonaufeld.at lookup read\n",
        encoding="utf-8",
    )

    rc = provisioner.main(
        [
            "--state-file",
            str(state_file),
            "--mailserver-dir",
            str(mailserver_dir),
            "--nextcloud-dir",
            str(nextcloud_dir),
        ]
    )

    assert rc == 0
    assert "share-remove" not in (tmp_path / "ctl_args.log").read_text()
    assert "--prune" not in (tmp_path / "configure_args.log").read_text()
