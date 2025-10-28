from gettext import gettext as _
from typing import List, cast

import streamlit as st

from lib.couchdb import couchdb
from lib.menu import menu
from lib.nextcloud.models.group import Group
from lib.nextcloud.models.user import NCUserList
from lib.nextcloud.models.protocol import Protocol
from lib.settings import (
    settings,
)
from lib.streamlit_oauth import load_user_data

# Streamlit app starts here
title = _("Groups").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="👥", layout="wide")

menu()
load_user_data()

db = couchdb()

user_list = NCUserList()

st.title(title)

groups = Group.get_all()

st.dataframe([g.model_dump() for g in groups], use_container_width=True)

groups = cast(List[Protocol], Protocol.get_all())

st.dataframe(
    [
        {
            x: getattr(g, x)
            for x in [
                "protocol_path",
                "group_name",
                "date",
                "moderated_by",
                "protocol_by",
                "participants",
            ]
        }
        for g in groups
    ],
    width="stretch",
)
