import datetime

from lib.nextcloud.collectives_loader import fetch_and_store_all_pages
from lib.nextcloud.collectives_parser import parse_pages
from lib.nextcloud.protocol import Protocol


def main():
    fetch_and_store_all_pages()

    # for group in Group.get_all():
    #     group.delete()
    for p in Protocol.get_all():
        p.delete()

    parse_pages()

    # NCUserList()
    # userlist.update_from_nextcloud()

    # group = Group.get_by_name("UG IT")
    # group.update_from_page()

    # only send reminder out of sleeping hours
    if not (8 < datetime.datetime.now().hour < 20):
        return


if __name__ == "__main__":
    main()
