from gettext import gettext as _
from typing import cast

import streamlit as st

from lib.couchdb import couchdb
from lib.menu import menu
from lib.nextcloud.models.group import Group
from lib.nextcloud.models.user import NCUserList
from lib.settings import settings
from lib.streamlit_oauth import load_user_data

# Streamlit app starts here
title = _("Groups").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="👥", layout="wide")

menu()
load_user_data()

db = couchdb()

user_list = NCUserList()

st.title(title)


def display_users(title: str, user_ids: list[str]):
    if user_ids:
        st.write(f"**{title}:**")
        for user_id in user_ids:
            user = user_list.get_user_by_uid(user_id)
            displayname = user_id
            if user and user.ocs and user.ocs.displayname:
                displayname = user.ocs.displayname
            st.write(f"- {displayname}")


def display_group(
    group: Group, user_list: NCUserList, all_groups: list[Group], level: int = 0
):
    """Display a group and its children."""
    with st.expander(f"{'➡️' * level} {group.name}", expanded=level == 0):
        if group.parent_group:
            st.write(f"**{_('Parent Group')}:** {group.parent_group}")

        if group.short_names:
            st.write(f"**{_('Short Names')}:** {', '.join(group.short_names)}")

        cols = st.columns(3)
        with cols[0]:
            display_users(_("Coordination"), group.coordination)
        with cols[1]:
            display_users(_("Delegates"), group.delegate)
        with cols[2]:
            display_users(_("Members"), group.members)

        children = [g for g in all_groups if g.parent_group == group.name]
        for child in children:
            display_group(child, user_list, all_groups, level + 1)


all_groups = cast(list[Group], Group.get_all())
top_level_groups = [g for g in all_groups if not g.parent_group]

for group in top_level_groups:
    display_group(group, user_list, all_groups)
