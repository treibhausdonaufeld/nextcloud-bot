import email
import imaplib
import logging
import re
from dataclasses import dataclass
from email.message import EmailMessage, Message
from typing import List, Set

from app.services.config import MailerConfig
from app.models.user import NCUserList
from app.settings import settings

from app.services.mail_sender import MailSender

logger = logging.getLogger(__name__)


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

    def __init__(self) -> None:
        if settings.mailinglist.smtp_server and not settings.mailinglist.from_address:
            raise ValueError("Mailinglist from_address is not configured")

    def fetch_maildata(self, nc_users: NCUserList, config: MailerConfig):
        mails_to_process = self._fetch_messages()

        for mail_message in mails_to_process:
            try:
                self.distribute_mail(mail_message.message, nc_users, config)
            except Exception:
                # A message left in the inbox is processed (and possibly sent)
                # again on the next iteration, so it is moved out no matter
                # what happened while handling it.
                logger.exception(
                    "Error handling message uid=%s, removing it from the inbox",
                    mail_message.uid,
                )
            finally:
                self.move_to_trash(mail_message.uid, config)

    def _fetch_messages(self) -> List[MailMessage]:
        """Fetch message objects from server which should be handled"""
        mail = self._login_imap()

        result, data = mail.uid("search", "", "ALL")  # fetch all mails

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

    def distribute_mail(
        self, message: Message, nc_users: NCUserList, config: MailerConfig
    ):
        """Distribute mail to all recipients"""
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

        info_address = (config.list_info_address or "").lower()
        if info_address and info_address in target_mailinglists:
            self._reply_with_list_overview(
                original_sender_email, sender_name, nc_users, config
            )
            return

        if config.restrict_sender:
            all_emails = nc_users.get_all_emails() | set(
                config.additional_allowed_senders
            )

            if (
                original_sender_email.lower() not in all_emails
                and not config.allows_domain(original_sender_email)
            ):
                logging.warning(
                    "Ignoring message from unauthorized sender %s",
                    original_sender_email,
                )
                return

        if config.reply_to_original_sender:
            if "Reply-To" in message:
                message.replace_header("Reply-To", message["From"])
            else:
                message["Reply-To"] = message["From"]

        # self._delete_original_headers(message)

        all_lists = config.lists

        for list_mail_addr in target_mailinglists:
            if list_mail_addr not in all_lists:
                logging.info(
                    "Recipient %s not in list of mailing-lists", list_mail_addr
                )
                continue

            list_config = all_lists[list_mail_addr]
            group_names = list_config.groups
            new_recipients = nc_users.mails_for_groups(group_names)

            message.replace_header(
                "Subject", list_config.prefix + " " + message["Subject"]
            )

            if not config.send_to_sender:
                new_recipients -= {original_sender_email}

            message.replace_header("From", settings.mailinglist.from_address)

            logger.info(
                "Forwarding mail '%s' as sender '%s' from %s to list %s (%s recipients)",
                message["Subject"],
                message["From"],
                sender_name,
                list_mail_addr,
                len(new_recipients),
            )
            self.forward_message(message, new_recipients)

    def _reply_with_list_overview(
        self,
        sender_email: str,
        sender_name: str,
        nc_users: NCUserList,
        config: MailerConfig,
    ) -> None:
        """Answer a request to the info address with every list and its size.

        Only enabled users from the database get an answer, so the overview
        cannot be pulled by anybody who happens to know the address.
        """
        sender = nc_users.get_user_by_email(sender_email)
        is_active_user = sender is not None and sender.enabled
        if not is_active_user and not config.allows_domain(sender_email):
            logging.warning(
                "Ignoring list overview request from unknown or inactive sender %s",
                sender_email,
            )
            return

        lines = [
            f"Hallo {sender_name},",
            "",
            "hier ist die Übersicht aller verfügbaren Mailinglisten:",
            "",
        ]
        for list_addr, list_config in sorted(config.lists.items()):
            group_mails = {
                name: nc_users.mails_for_group(name) - {""}
                for name in list_config.groups
            }
            recipients = set().union(*group_mails.values()) if group_mails else set()
            prefix = f"{list_config.prefix} " if list_config.prefix else ""
            lines.append(f"{prefix}{list_addr} – {len(recipients)} Empfänger:innen")
            for name, mails in group_mails.items():
                lines.append(f"    – {name}: {len(mails)}")
        lines += [
            "",
            "Um an eine Liste zu schreiben, sende eine E-Mail an die jeweilige Adresse.",
        ]

        reply = EmailMessage()
        reply["From"] = settings.mailinglist.from_address
        reply["To"] = sender_email
        reply["Subject"] = "Übersicht der Mailinglisten"
        reply.set_content("\n".join(lines))

        try:
            MailSender().send(reply, sender_email)
        except Exception:
            logging.exception("Failed to send list overview to %s", sender_email)

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

        mailer = MailSender()
        for recipient in recipients:
            mailer.send(message, recipient)

    # Conventional trash mailbox name used when the server does not flag one
    # via SPECIAL-USE (RFC 6154). DMS/Dovecot uses "/" as hierarchy separator
    # here (see the mailserver's dovecot.cf), so it is a top-level "Trash" and
    # not "INBOX.Trash".
    DEFAULT_TRASH_FOLDER = "Trash"

    def move_to_trash(self, uid: str, config: MailerConfig):
        """Move a processed message out of the inbox.

        Runs for every fetched message - whether it was forwarded, answered,
        ignored or failed - so it is not handled (and sent) again. The
        message goes to Trash when possible and is permanently deleted
        otherwise; either way it leaves the inbox.
        """
        mail = self._login_imap()
        try:
            folder = self._trash_folder(mail, config)
            if self._uid_move(mail, uid, folder):
                logger.info("Moved message %s to %s", uid, folder)
            else:
                logger.warning(
                    "Could not move message %s to %s, deleting it instead",
                    uid,
                    folder,
                )
                mail.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
            mail.expunge()
        except Exception:
            logger.exception("Failed to remove processed message uid=%s", uid)
        finally:
            try:
                mail.close()
            except Exception:
                logger.debug("Failed to close mailbox after processing %s", uid)
            mail.logout()

    def _trash_folder(self, mail, config: MailerConfig) -> str:
        """Resolve the mailbox processed messages should be moved to."""
        if config.trash_folder:
            return config.trash_folder
        return self._special_use_folder(mail, "\\Trash") or self.DEFAULT_TRASH_FOLDER

    def _special_use_folder(self, mail, flag: str) -> str | None:
        """Return the folder the server advertises for a SPECIAL-USE flag."""
        try:
            status, folders = mail.list('""', "*")
        except imaplib.IMAP4.error:
            return None
        if status != "OK" or not folders:
            return None

        for entry in folders:
            if not entry:
                continue
            line = (
                entry.decode("utf-8", "replace")
                if isinstance(entry, bytes)
                else str(entry)
            )
            if flag not in line:
                continue
            match = re.match(r'\([^)]*\)\s+"[^"]*"\s+(?P<name>.+)', line)
            if match:
                return match.group("name").strip().strip('"')
        return None

    def _uid_move(self, mail, uid: str, folder: str) -> bool:
        """Move the message with UID `uid` to `folder`; return whether it did."""
        self._ensure_folder(mail, folder)
        target = self._quote(folder)

        try:
            status, _ = mail.uid("MOVE", uid, target)
            if status == "OK":
                return True
        except imaplib.IMAP4.error:
            # Server without the MOVE extension: fall back to COPY + delete.
            logger.debug("UID MOVE unsupported, falling back to COPY")

        status, _ = mail.uid("COPY", uid, target)
        if status != "OK":
            return False
        mail.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        return True

    @staticmethod
    def _ensure_folder(mail, folder: str) -> None:
        try:
            mail.create(MailFetcher._quote(folder))
        except imaplib.IMAP4.error:
            # Already exists (or creating is not permitted); the move itself
            # will report the truth if the folder is unusable.
            pass

    @staticmethod
    def _quote(name: str) -> str:
        return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'

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
            logging.exception("Error extracting recipients for: %s", mail_data.__dict__)
            return set()

    def _login_imap(self):
        mail = imaplib.IMAP4_SSL(settings.mailinglist.imap_server)
        mail.login(
            settings.mailinglist.imap_username, settings.mailinglist.imap_password
        )
        mail.select("INBOX")  # connect to inbox.

        return mail
