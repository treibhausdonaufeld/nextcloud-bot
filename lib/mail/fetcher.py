import email
import imaplib
import logging
import re
from dataclasses import dataclass
from email.message import Message
from typing import List, Set

from .config import Config
from .nc_users import NCUserList
from .sender import MailSender


@dataclass(init=True)
class MailMessage:
    uid: str
    message: Message


def is_autoreply(message: Message) -> bool:
    return (
        message.get("X-Autoreply", "").lower() == "yes"
        or message.get("Auto-Submitted", "") == "auto-replied"
    )


class MailFetcher:
    """Fetch mail from server and start processing"""

    mail_regex = re.compile(r"[a-zA-Z0-9&_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

    def fetch_maildata(self, nc_users: NCUserList):
        mails_to_process = self._fetch_messages()

        for mail_message in mails_to_process:
            self.distribute_mail(mail_message.message, nc_users)
            self.move_to_archive(mail_message.uid)

    def _fetch_messages(self) -> List[MailMessage]:
        """Fetch message objects from server which should be handled"""
        mail = self._login_imap()

        result, data = mail.uid("search", None, "ALL")  # fetch all mails

        mails_to_process = []

        if result == "OK" and len(data[0].split()) > 0:
            logging.info("Received {} mails".format(len(data[0].split())))
            for uid in data[0].split():
                result, mail_data = mail.uid("fetch", uid, "(RFC822)")
                message: Message = email.message_from_bytes(mail_data[0][1])
                mails_to_process.append(MailMessage(uid=uid, message=message))

        mail.close()
        mail.logout()

        return mails_to_process

    def distribute_mail(self, message: Message, nc_users: NCUserList):
        """Distribute mail to all recipients"""
        config = Config.data["distribution"]

        target_mailinglists = self._extract_recipients(message)

        original_sender_email = self.mail_regex.findall(message["From"])[0]
        sender_name = message["From"].split("<")[0].strip() or original_sender_email

        if original_sender_email.lower().startswith("mailer-daemon@"):
            logging.warning(
                "Ignoring message from mailer-daemon: %s", message["Subject"]
            )
            return

        if is_autoreply(message):
            logging.warning("Ignoring autoreply message: %s", message["From"])
            return

        if config.get("reply_to_original_sender", True):
            if "Reply-To" in message:
                message.replace_header("Reply-To", message["From"])
            else:
                message["Reply-To"] = message["From"]

        # self._delete_original_headers(message)

        from_addr = config.get("from_addr", "").replace("ORIGINAL_SENDER", sender_name)

        all_lists = config["lists"]

        for list_mail_addr in target_mailinglists:
            if list_mail_addr not in all_lists:
                logging.info(
                    "Recipient %s not in list of mailing-lists", list_mail_addr
                )
                continue

            list_config = all_lists[list_mail_addr]
            group_names = list_config["groups"]
            new_recipients = nc_users.mails_for_groups(group_names)

            message.replace_header(
                "Subject", list_config.get("prefix", "") + " " + message["Subject"]
            )

            if not config.get("send_to_sender", True):
                new_recipients -= {original_sender_email}

            if from_addr:
                message.replace_header(
                    "From", from_addr.replace("LIST_NAME", f"<{list_mail_addr}>")
                )
            self.forward_message(message, new_recipients)

    def _delete_original_headers(self, message):
        # delete all original message headers
        headers_to_keep = {
            "reply-to",
            "from",
            "subject",
            "to",
            "content-type",
            "content-transfer-encoding",
            "mime-version",
        }
        for key in message.keys():
            if key.lower() not in headers_to_keep:
                del message[key]

    def forward_message(self, message: Message, recipients: Set[str]):
        """Forward given message as sent from list"""
        logging.debug("Forwarding mail to %s", recipients)

        for recipient in recipients:
            MailSender.send(message, recipient)

    def move_to_archive(self, uid: str):
        """Move processed message to archive"""
        mail = self._login_imap()

        archive_folder = "INBOX.Archive"
        apply_lbl_msg = mail.uid("COPY", uid, archive_folder)
        if apply_lbl_msg[0] == "OK":
            logging.info("Message moved to folder %s", archive_folder)
            mail.uid("STORE", uid, "+FLAGS", "(\\Deleted)")

        mail.expunge()
        mail.close()
        mail.logout()

    def _extract_recipients(self, mail_data: Message) -> Set[str]:
        """Return set of all recipients of the message"""
        try:
            return set(
                x.lower()
                for x in set(
                    self.mail_regex.findall(mail_data["X-Original-To"] or "")
                    + self.mail_regex.findall(mail_data["To"] or "")
                    + self.mail_regex.findall(mail_data["Cc"] or "")
                )
            )
        except Exception:
            logging.exception("Error extracting recipients for: %s", mail_data._headers)
            return set()

    def _login_imap(self):
        config = Config.data["imap"]

        mail = imaplib.IMAP4_SSL(config["host"])
        mail.login(config["username"], config["password"])
        mail.select("INBOX")  # connect to inbox.

        return mail
