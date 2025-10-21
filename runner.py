import datetime

from lib.nextcloud.collectives_loader import fetch_and_store_all_pages


def main():
    fetch_and_store_all_pages()

    # only send reminder out of sleeping hours
    if not (8 < datetime.datetime.now().hour < 20):
        return


if __name__ == "__main__":
    main()
