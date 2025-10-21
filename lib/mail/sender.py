import logging
import smtplib
import time
from email.message import Message

from .config import Config


class MailSender:
    """Class to send mail to certain recipients"""

    @staticmethod
    def send(message: Message, to_addr: str):
        """Send a mail message to given to_addr"""
        config = Config.data["smtp"]

        message.replace_header("To", to_addr)

        # open authenticated SMTP connection and send message with
        # specified envelope from and to addresses
        smtp = smtplib.SMTP(config["host"], config["port"])
        smtp.starttls()
        smtp.login(config["username"], config["password"])
        smtp.sendmail(message["From"], to_addr, message.as_string())
        smtp.quit()

        logging.info("Successfully sent %s to %s", message["Subject"], to_addr)

        delay_seconds = int(config["send_delay_seconds"])
        logging.debug("Sleeping %d seconds", delay_seconds)
        time.sleep(delay_seconds)
