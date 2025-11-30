import pandas as pd
import plotly.express as px
import streamlit as st

from lib.couchdb import couchdb
from lib.menu import menu
from lib.nextcloud.models.user import NCUserList
from lib.settings import _, settings
from lib.streamlit_oauth import load_user_data

# Streamlit app starts here
title = _("Mentions").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="📣", layout="wide")

menu()
load_user_data()

db = couchdb()
user_list = NCUserList()

st.title(title)

# Calculate mentions
mention_counts = []
users = list(user_list.users.values())

# Progress bar
progress_bar = st.progress(0)
status_text = st.empty()
total_users = len(users)

for i, user in enumerate(users):
    status_text.text(f"{_('Processing')} {user.ocs.displayname}...")

    try:
        # Using include_docs=False for performance as we only need the count
        user_view_result = db.query(
            "mentions/by_user", key=user.username, reduce=False, include_docs=False
        )
        count = len(list(user_view_result))

        if count > 0:
            mention_counts.append(
                {_("User"): user.ocs.displayname, _("Mentions"): count}
            )

    except Exception as e:
        st.error(f"Error processing {user.username}: {e}")

    progress_bar.progress((i + 1) / total_users)

status_text.empty()
progress_bar.empty()

if mention_counts:
    df = pd.DataFrame(mention_counts)
    df_sorted = df.sort_values(by=_("Mentions"), ascending=False)

    st.dataframe(df_sorted, hide_index=True, width="stretch")

    fig = px.bar(
        df_sorted,
        x=_("Mentions"),
        y=_("User"),
        orientation="h",
        title=_("Mentions per User"),
        text=_("Mentions"),
    )
    fig.update_layout(
        yaxis={"categoryorder": "total ascending"}, height=max(400, len(df_sorted) * 25)
    )
    st.plotly_chart(fig, width="stretch")
else:
    st.info(_("No mentions found."))
