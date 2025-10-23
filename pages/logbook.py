from gettext import gettext as _

import streamlit as st

from lib.menu import menu
from lib.settings import (
    settings,
)
from lib.streamlit_oauth import load_user_data

# Streamlit app starts here
title = _("Logbook").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="👥", layout="wide")

menu()
load_user_data()
