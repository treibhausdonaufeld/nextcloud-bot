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
    ctl = mailserver_dir / "mailbox_ctl.sh"
    ctl.write_text(
        "#!/bin/sh\n"
        'cmd="$1"; shift\n'
        'case "$cmd" in\n'
        f'  create-shared) echo "create-shared $*" >> "{args_log}"; echo "created $1" ;;\n'
        f"  share-add)\n"
        f'    echo "share-add $*" >> "{args_log}"\n'
        '    for login in "$@"; do\n'
        '      printf "%s\\tpw-%s\\n" "$login@treibhausdonaufeld.at" "$login"\n'
        "    done\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    ctl.chmod(0o755)

    configure = nextcloud_dir / "mailbox_configure.sh"
    configure.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
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


def test_provisioner_applies_and_records_credentials(tmp_path):
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
        ]
    )

    assert rc == 0
    status = json.loads((tmp_path / "mailboxes.status.json").read_text())
    assert status["failed"] == 0
    assert status["mailboxes"][0]["ok"] is True
    assert all(user["ok"] for user in status["mailboxes"][0]["users"])

    share_args = [
        line
        for line in (tmp_path / "ctl_args.log").read_text().splitlines()
        if line.startswith("share-add")
    ]
    assert share_args == [
        "share-add office@treibhausdonaufeld.at fabian.helm gabriele.adebisi-schuster"
    ]

    passwords = (tmp_path / "mailboxes.passwords.txt").read_text()
    assert "fabian.helm@treibhausdonaufeld.at\tpw-fabian.helm" in passwords
    assert (
        "gabriele.adebisi-schuster@treibhausdonaufeld.at\tpw-gabriele.adebisi-schuster"
        in passwords
    )
    assert (tmp_path / "mailboxes.passwords.txt").stat().st_mode & 0o777 == 0o600
