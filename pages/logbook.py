from gettext import gettext as _
from typing import List

import streamlit as st

from lib.menu import menu
from lib.nextcloud.models.decision import Decision
from lib.settings import (
    settings,
)
from lib.streamlit_oauth import load_user_data


@st.cache_data(ttl=3600)
def get_all_decisions() -> List[Decision]:
    return Decision.get_all()


# Streamlit app starts here
title = _("Logbook").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="👥", layout="wide")

menu()
load_user_data()

decisions = get_all_decisions()

st.title(title)
