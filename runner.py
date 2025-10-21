import argparse
import datetime

import requests

from lib.common import settings


def main():
    # Create the parser
    parser = argparse.ArgumentParser(description="Run a script.")

    # Add optional arguments
    parser.add_argument(
        "--update-data", "-u", action="store_true", help="Run update nuki data"
    )
    parser.add_argument(
        "--send-reminder-date",
        "-s",
        action="store_true",
        help="Send plenum date reminder",
    )
    parser.add_argument(
        "--send-reminder-money", "-m", action="store_true", help="Send money reminders"
    )

    # if signal is configured, make sure to always fetch latest messages regularily
    if settings.signal.receive_url:
        response = requests.get(settings.signal.receive_url)
        if response.ok:
            print("Received messages from signal")
        else:
            print(f"Error receiving messages from signal: {response.text}")

    # only send reminder out of sleeping hours
    if not (8 < datetime.datetime.now().hour < 20):
        return


if __name__ == "__main__":
    main()
