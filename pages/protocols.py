from gettext import gettext as _
from typing import List, cast

import streamlit as st

from lib.couchdb import couchdb
from lib.menu import menu
from lib.nextcloud.models.protocol import Protocol
from lib.nextcloud.models.collective_page import CollectivePage
from lib.nextcloud.models.user import NCUserList
from lib.settings import (
    settings,
)
from datetime import datetime
from lib.streamlit_oauth import load_user_data

# Streamlit app starts here
title = _("Protocols").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="📝")

menu()
load_user_data()

db = couchdb()

user_list = NCUserList()
user_list.load_users()

st.title(title)

protocols = cast(List[Protocol], Protocol.get_all())

# filter out protocols in the future
now_str = datetime.now().strftime("%Y-%m-%d")

# sort by parsed date (fallback to string) descending
protocols = sorted(
    [p for p in protocols if p.date <= now_str], key=lambda p: p.date, reverse=True
)

groups = list(set(p.group_name for p in protocols if p.group_name))
groups.sort()

selected_group = st.selectbox(_("Filter by group"), [""] + groups)

if selected_group:
    protocols = [p for p in protocols if p.group_name == selected_group]


def display_users(user_ids: list[str]):
    names = []
    for user_id in user_ids:
        user = user_list.get_user_by_uid(user_id)
        display_name = user_id
        if user and user.ocs and user.ocs.display_name:
            display_name = user.ocs.display_name
        names.append(display_name)
    return ", ".join(names)


for protocol in protocols:
    with st.expander(f"{protocol.date} - {protocol.group_name}"):
        # show link to the protocol page when available
        page = None
        try:
            if protocol.page_id:
                page = CollectivePage.get_from_page_id(protocol.page_id)
        except Exception:
            page = None

        if page and getattr(page, "url", None):
            st.markdown(f"**{_('Link')}:** [{protocol.protocol_path}]({page.url})")
        else:
            st.markdown(f"**{_('Path')}:** `{protocol.protocol_path}`")
        st.markdown(f"**{_('Moderated by')}:** {display_users(protocol.moderated_by)}")
        st.markdown(f"**{_('Protocol by')}:** {display_users(protocol.protocol_by)}")
        st.markdown(f"**{_('Participants')}:** {display_users(protocol.participants)}")
