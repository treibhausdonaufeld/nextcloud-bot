from gettext import gettext as _
from typing import List

import streamlit as st

from lib.menu import menu
from lib.nextcloud.models.decision import Decision
from lib.settings import (
    settings,
)
from lib.streamlit_oauth import load_user_data


# @st.cache_data(ttl=3600)
def get_all_decisions() -> List[Decision]:
    return Decision.get_all()


# Streamlit app starts here
title = _("Logbook").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="✅", layout="wide")

menu()
load_user_data()

decisions = get_all_decisions()

st.title(title)

# Sort decisions by date (newest first)
decisions.sort(key=lambda d: d.date, reverse=True)

# Get unique group names for filtering
group_names = list(set(d.group_name for d in decisions if d.group_name))
group_names.sort()

# Add filter for group
selected_group = st.selectbox(_("Filter by group"), [""] + group_names)

if selected_group:
    decisions = [d for d in decisions if d.group_name == selected_group]


df = {
    _("Date"): [d.date for d in decisions],
    _("Title"): [d.title for d in decisions],
    _("Text"): [d.text for d in decisions],
    _("Group"): [d.group_name for d in decisions],
    _("Link"): [d.page.url if d.page else d.external_link or "" for d in decisions],
}

st.dataframe(
    df,
    column_config={
        _("Date"): st.column_config.DateColumn(
            _("Date"),
            format="YYYY-MM-DD",
        ),
        _("Link"): st.column_config.LinkColumn(
            _("Link"), display_text="Open protocol", max_chars=30
        ),
    },
    hide_index=True,
)

# Display decisions
# for decision in decisions:
#     with st.expander(f"{decision.date} - {decision.title} ({decision.group_name})"):
#         if decision.text:
#             st.markdown(f"**{_('Description')}:** {decision.text}")

#         st.markdown(f"**{_('Group')}:** {decision.group_name}")
#         st.markdown(f"**{_('Group ID')}:** {decision.group_id}")
#         st.markdown(f"**{_('Protocol ID')}:** {decision.protocol_id}")

#         if decision.external_link:
#             st.markdown(f"**{_('External Link')}:** [Link]({decision.external_link})")
