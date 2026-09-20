"""Tests for MailFetcher mailing-list distribution and the overview reply."""

import email
import imaplib
from email.message import Message

import pytest

from app.models.user import NCUser
from app.services.config import MailerConfig, MailerListItem
from app.services import mail_fetcher
from app.services.mail_fetcher import MailFetcher, MailMessage

INFO_ADDRESS = "list@treibhausdonaufeld.at"


class FakeUserList:
    """Minimal stand-in for NCUserList (no database access)."""

    def __init__(self, users, group_mails):
        self.users = {u.username: u for u in users}
        self.group_mails = group_mails

    def get_user_by_email(self, email):
        email = (email or "").lower()
        return next(
            (u for u in self.users.values() if u.email and u.email.lower() == email),
            None,
        )

    def mails_for_group(self, group_name):
        return set(self.group_mails.get(group_name, set()))

    def mails_for_groups(self, group_names):
        result = set()
        for name in group_names:
            result |= self.mails_for_group(name)
        return result

    def get_all_emails(self):
        return {u.email.lower() for u in self.users.values() if u.email}


def make_message(to, sender="Sender <sender@example.com>", subject="Hallo"):
    raw = (
        f"From: {sender}\n"
        f"To: {to}\n"
        f"Subject: {subject}\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Body\n"
    )
    return email.message_from_string(raw)


def make_config(**kwargs):
    return MailerConfig(
        lists={
            INFO_ADDRESS: MailerListItem(prefix="[THD]", groups=["Mitglieder"]),
            "list+ka@treibhausdonaufeld.at": MailerListItem(
                prefix="[THD-KA]", groups=["AG KA"]
            ),
        },
        **kwargs,
    )


@pytest.fixture
def sent(monkeypatch):
    """Capture mails sent via MailSender and return the (message, to) tuples."""
    messages = []

    class FakeMailSender:
        def send(self, message: Message, to_addr: str):
            messages.append((message, to_addr))

    monkeypatch.setattr(mail_fetcher, "MailSender", FakeMailSender)
    return messages


@pytest.fixture
def forwarded(monkeypatch):
    """Capture forward_message calls and return the recipient sets."""
    recipients = []

    def fake_forward(self, message, new_recipients):
        recipients.append(new_recipients)

    monkeypatch.setattr(MailFetcher, "forward_message", fake_forward)
    return recipients


def test_info_address_replies_with_all_lists_and_counts(sent, forwarded):
    sender = NCUser(username="sender", email="sender@example.com", enabled=True)
    users = FakeUserList(
        [sender],
        {
            "Mitglieder": {"a@example.com", "sender@example.com"},
            "AG KA": {"a@example.com"},
        },
    )

    MailFetcher().distribute_mail(make_message(INFO_ADDRESS), users, make_config())

    assert forwarded == []
    assert len(sent) == 1
    reply, to_addr = sent[0]
    assert to_addr == "sender@example.com"
    assert reply["To"] == "sender@example.com"

    body = reply.get_content()
    assert INFO_ADDRESS in body
    assert "list+ka@treibhausdonaufeld.at" in body
    assert "[THD] " in body
    assert "2 Empfänger:innen" in body
    assert "1 Empfänger:innen" in body
    assert "– Mitglieder: 2" in body
    assert "– AG KA: 1" in body


def test_info_address_breakdown_counts_each_group_but_dedupes_total(sent, forwarded):
    sender = NCUser(username="sender", email="sender@example.com", enabled=True)
    users = FakeUserList(
        [sender],
        {
            "A": {"a@example.com", "shared@example.com"},
            "B": {"shared@example.com", "b@example.com"},
        },
    )
    config = MailerConfig(
        lists={
            INFO_ADDRESS: MailerListItem(prefix="[THD]", groups=["A", "B"]),
        }
    )

    MailFetcher().distribute_mail(make_message(INFO_ADDRESS), users, config)

    body = sent[0][0].get_content()
    # Three distinct recipients, but each group reports its own size.
    assert "– 3 Empfänger:innen" in body
    assert "– A: 2" in body
    assert "– B: 2" in body


def test_info_address_ignores_inactive_sender(sent, forwarded):
    inactive = NCUser(username="sender", email="sender@example.com", enabled=False)
    users = FakeUserList([inactive], {"Mitglieder": {"sender@example.com"}})

    MailFetcher().distribute_mail(make_message(INFO_ADDRESS), users, make_config())

    assert sent == []
    assert forwarded == []


def test_info_address_ignores_unknown_sender(sent, forwarded):
    users = FakeUserList([], {"Mitglieder": {"a@example.com"}})

    MailFetcher().distribute_mail(make_message(INFO_ADDRESS), users, make_config())

    assert sent == []
    assert forwarded == []


def test_info_address_allows_whitelisted_domain(sent, forwarded):
    users = FakeUserList([], {"Mitglieder": {"a@example.com"}})
    config = make_config(allowed_domains=["treibhausdonaufeld.at"])

    MailFetcher().distribute_mail(
        make_message(INFO_ADDRESS, sender="Someone <someone@treibhausdonaufeld.at>"),
        users,
        config,
    )

    assert len(sent) == 1
    assert sent[0][1] == "someone@treibhausdonaufeld.at"
    assert INFO_ADDRESS in sent[0][0].get_content()


def test_info_address_rejects_domain_not_on_whitelist(sent, forwarded):
    users = FakeUserList([], {"Mitglieder": {"a@example.com"}})
    config = make_config(allowed_domains=["treibhausdonaufeld.at"])

    MailFetcher().distribute_mail(
        make_message(INFO_ADDRESS, sender="Someone <someone@example.com>"),
        users,
        config,
    )

    assert sent == []
    assert forwarded == []


def test_info_address_can_be_disabled(sent, forwarded):
    sender = NCUser(username="sender", email="sender@example.com", enabled=True)
    users = FakeUserList([sender], {"Mitglieder": {"sender@example.com"}})

    MailFetcher().distribute_mail(
        make_message(INFO_ADDRESS),
        users,
        make_config(list_info_address="", send_to_sender=True),
    )

    assert sent == []
    # With the overview disabled the info address is treated as a normal list.
    assert forwarded == [{"sender@example.com"}]


def test_other_lists_still_distribute(sent, forwarded):
    sender = NCUser(username="sender", email="sender@example.com", enabled=True)
    users = FakeUserList(
        [sender],
        {"Mitglieder": {"sender@example.com"}, "AG KA": {"ka@example.com"}},
    )

    MailFetcher().distribute_mail(
        make_message("list+ka@treibhausdonaufeld.at"), users, make_config()
    )

    assert sent == []
    assert forwarded == [{"ka@example.com"}]


def test_restrict_sender_allows_whitelisted_domain(sent, forwarded):
    users = FakeUserList([], {"AG KA": {"ka@example.com"}})
    config = make_config(
        restrict_sender=True, allowed_domains=["treibhausdonaufeld.at"]
    )

    MailFetcher().distribute_mail(
        make_message(
            "list+ka@treibhausdonaufeld.at",
            sender="Someone <someone@treibhausdonaufeld.at>",
        ),
        users,
        config,
    )

    assert forwarded == [{"ka@example.com"}]


def test_restrict_sender_rejects_other_domain(sent, forwarded):
    users = FakeUserList([], {"AG KA": {"ka@example.com"}})
    config = make_config(
        restrict_sender=True, allowed_domains=["treibhausdonaufeld.at"]
    )

    MailFetcher().distribute_mail(
        make_message("list+ka@treibhausdonaufeld.at", sender="Someone <x@example.com>"),
        users,
        config,
    )

    assert forwarded == []


class FakeIMAP:
    """Minimal IMAP stand-in recording what the fetch/move code does."""

    def __init__(self, trash_flagged=True, move_supported=True, copy_ok=True):
        self.trash_flagged = trash_flagged
        self.move_supported = move_supported
        self.copy_ok = copy_ok
        self.created = []
        self.moved = []
        self.copied = []
        self.stored = []
        self.expunged = False
        self.closed = False
        self.logged_out = False
        self.selected = None

    def login(self, username, password):
        return "OK", [b""]

    def select(self, mailbox="INBOX"):
        self.selected = mailbox
        return "OK", [b"1"]

    def list(self, reference, pattern):
        if self.trash_flagged:
            return "OK", [b'(\\HasNoChildren \\Trash) "/" Trash']
        return "OK", [b'(\\HasNoChildren \\Sent) "/" Sent']

    def create(self, name):
        self.created.append(name)
        return "OK", [b""]

    def uid(self, command, *args):
        command = command.upper()
        if command == "MOVE":
            if not self.move_supported:
                raise imaplib.IMAP4.error("MOVE unsupported")
            self.moved.append(args)
            return "OK", [b""]
        if command == "COPY":
            if not self.copy_ok:
                return "NO", [b"failed"]
            self.copied.append(args)
            return "OK", [b""]
        if command == "STORE":
            self.stored.append(args)
            return "OK", [b""]
        return "OK", [b""]

    def expunge(self):
        self.expunged = True
        return "OK", [b""]

    def close(self):
        self.closed = True

    def logout(self):
        self.logged_out = True


class FakeImapFetcher(MailFetcher):
    """MailFetcher whose IMAP connection is a fake."""

    def __init__(self, fake: FakeIMAP):
        super().__init__()
        self.fake = fake

    def _login_imap(self):
        return self.fake


class RecordingFetcher(MailFetcher):
    """MailFetcher with stubbed distribution that records moved uids."""

    def __init__(self, messages, distribute):
        super().__init__()
        self.messages = messages
        self.distribute = distribute
        self.moved = []

    def _fetch_messages(self):
        return self.messages

    def distribute_mail(self, message, nc_users, config):
        return self.distribute(message, nc_users, config)

    def move_to_trash(self, uid, config):
        self.moved.append(uid)


def fetcher_with(fake: FakeIMAP) -> MailFetcher:
    return FakeImapFetcher(fake)


def test_move_to_trash_uses_special_use_folder():
    fake = FakeIMAP(trash_flagged=True)

    fetcher_with(fake).move_to_trash("42", MailerConfig())

    assert fake.moved == [("42", '"Trash"')]
    assert fake.stored == []
    assert fake.expunged and fake.closed and fake.logged_out


def test_move_to_trash_uses_configured_folder_and_creates_it():
    fake = FakeIMAP(trash_flagged=False)

    fetcher_with(fake).move_to_trash("7", MailerConfig(trash_folder="My Trash"))

    assert fake.created == ['"My Trash"']
    assert fake.moved == [("7", '"My Trash"')]


def test_move_to_trash_defaults_to_trash_without_special_use():
    fake = FakeIMAP(trash_flagged=False)

    fetcher_with(fake).move_to_trash("1", MailerConfig())

    assert fake.moved == [("1", '"Trash"')]


def test_move_to_trash_falls_back_to_copy_without_move():
    fake = FakeIMAP(move_supported=False)

    fetcher_with(fake).move_to_trash("9", MailerConfig())

    assert fake.copied == [("9", '"Trash"')]
    assert ("9", "+FLAGS", "(\\Deleted)") in fake.stored
    assert fake.expunged


def test_move_to_trash_deletes_when_move_and_copy_fail():
    fake = FakeIMAP(move_supported=False, copy_ok=False)

    fetcher_with(fake).move_to_trash("11", MailerConfig())

    assert fake.copied == []
    assert ("11", "+FLAGS", "(\\Deleted)") in fake.stored
    assert fake.expunged


def test_fetch_maildata_removes_message_even_when_distribution_fails():
    def boom(message, nc_users, config):
        raise RuntimeError("smtp down")

    fetcher = RecordingFetcher(
        [MailMessage(uid="5", message=email.message_from_string("From: a@b\n\nbody"))],
        boom,
    )

    fetcher.fetch_maildata(FakeUserList([], {}), MailerConfig())

    assert fetcher.moved == ["5"]


def test_fetch_maildata_removes_message_when_recipient_is_ignored():
    fetcher = RecordingFetcher(
        [MailMessage(uid="6", message=email.message_from_string("From: a@b\n\nbody"))],
        lambda message, nc_users, config: None,
    )

    fetcher.fetch_maildata(FakeUserList([], {}), MailerConfig())

    assert fetcher.moved == ["6"]
