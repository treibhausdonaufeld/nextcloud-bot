"""Tests for MailFetcher mailing-list distribution and the overview reply."""

import email
from email.message import Message

import pytest

from app.models.user import NCUser
from app.services.config import MailerConfig, MailerListItem
from app.services import mail_fetcher
from app.services.mail_fetcher import MailFetcher

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
