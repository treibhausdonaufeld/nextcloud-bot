from gettext import gettext as _

import streamlit as st

from lib.couchdb import couchdb
from lib.menu import menu
from lib.nextcloud.nc_users import NCUserList
from lib.nextcloud.protocol import Group, Protocol
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

groups = Protocol.get_all()

st.dataframe([g.model_dump() for g in groups], use_container_width=True)
