import pandas as pd
import plotly.express as px
import streamlit as st

from lib.couchdb import couchdb
from lib.menu import menu
from lib.nextcloud.models.collective_page import CollectivePage, PageSubtype
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
        # Using include_docs=True to get page details
        user_view_result = db.query(
            "mentions/by_user", key=user.username, reduce=False, include_docs=True
        )
        rows = list(user_view_result)
        count = len(rows)

        if count > 0:
            pages = {}
            for row in rows:
                try:
                    if "doc" in row:
                        page = CollectivePage(**row["doc"])
                        # Use page ID to deduplicate
                        if page.ocs and page.ocs.id:
                            pages[page.ocs.id] = page
                except Exception:
                    continue

            distinct_pages_count = len(pages)
            protocol_count = 0
            groups = set()

            for page in pages.values():
                if page.subtype == PageSubtype.PROTOCOL:
                    protocol_count += 1
                    # Try to extract group from title: "YYYY-MM-DD Group Name"
                    if page.title and " " in page.title:
                        parts = page.title.split(" ", 1)
                        if len(parts) == 2:
                            groups.add(parts[1])

            mention_counts.append(
                {
                    _("User"): user.ocs.displayname,
                    _("Mentions"): count,
                    _("Distinct Pages"): distinct_pages_count,
                    _("Distinct Protocols"): protocol_count,
                    _("Groups"): ", ".join(sorted(groups)),
                }
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
