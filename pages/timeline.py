import pandas as pd
import plotly.express as px
import streamlit as st

from lib.couchdb import couchdb
from lib.menu import menu
from lib.nextcloud.models.user import NCUserList
from lib.settings import _, settings
from lib.streamlit_oauth import load_user_data

# Streamlit app starts here
title = _("Timeline").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="⌛", layout="wide")

menu()
load_user_data()

db = couchdb()

user_list = NCUserList()

st.title(title)


st.set_page_config(layout="wide")


with open("milestones.md", "r", encoding="utf-8") as f:
    md_text = f.read()


def parse_markdown_tables(md_text):
    """Parse headers and their following markdown tables into a dict:
    {header: [row_dict, ...], ...}
    """
    lines = md_text.splitlines()
    sections = {}
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#"):
            header = line.lstrip("#").strip()
            # advance to next pipe-line (table start)
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("|"):
                i += 1
            if i >= len(lines):
                sections[header] = []
                continue
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].rstrip())
                i += 1
            # parse table_lines
            if len(table_lines) >= 2:
                hdr_cells = [c.strip() for c in table_lines[0].strip("|").split("|")]
                rows = []
                for tr in table_lines[2:]:
                    cells = [c.strip() for c in tr.strip("|").split("|")]
                    # pad
                    if len(cells) < len(hdr_cells):
                        cells += [""] * (len(hdr_cells) - len(cells))
                    row = {
                        h: (cells[idx] if idx < len(cells) else "")
                        for idx, h in enumerate(hdr_cells)
                    }
                    rows.append(row)
                sections[header] = rows
            else:
                sections[header] = []
        else:
            i += 1
    return sections


sections = parse_markdown_tables(md_text)

tabs = st.tabs(list(sections.keys())) if sections else []

for tab, header in zip(tabs, sections.keys()):
    with tab:
        rows = sections.get(header, [])
        parsed = []
        for r in rows:
            # normalize keys and lookup case-insensitively
            key_map = {k.strip().lower(): v for k, v in r.items()}
            start = key_map.get("start", "").strip() or None
            end = key_map.get("end", "").strip() or None
            group = key_map.get("group", "").strip() or header
            title = key_map.get("title", "").strip() or ""

            # if start missing skip
            if not start:
                continue

            # if end missing, set to today
            if not end:
                end = pd.Timestamp.now().strftime("%Y-%m-%d")

            parsed.append({"start": start, "end": end, "group": group, "title": title})

        df = pd.DataFrame(parsed)
        if not df.empty:
            df["start"] = pd.to_datetime(df["start"], errors="coerce")
            df["end"] = pd.to_datetime(df["end"], errors="coerce")
            # where end invalid, set to current date
            df.loc[df["end"].isna(), "end"] = pd.Timestamp.now()
            df = df.dropna(subset=["start"])

            # order rows by group (alphabetically) and then by start date
            group_order = sorted(
                df["group"].dropna().unique(), key=lambda s: str(s).lower()
            )
            df["group"] = pd.Categorical(
                df["group"], categories=group_order, ordered=True
            )
            df = df.sort_values(["group", "start"])

            # assign track numbers within each group to prevent overlaps
            tracks_list = []
            for group_name in group_order:
                group_df = df[df["group"] == group_name].copy()
                track_ends: list[tuple[int, pd.Timestamp]] = []
                for idx, row in group_df.iterrows():
                    start = row["start"]
                    end = row["end"]
                    # find first available track (where track ends before this start)
                    assigned_track = None
                    for i, (track_num, track_end) in enumerate(track_ends):
                        if track_end <= start:
                            assigned_track = track_num
                            track_ends[i] = (track_num, end)
                            break
                    if assigned_track is None:
                        # need a new track
                        assigned_track = len(track_ends)
                        track_ends.append((assigned_track, end))
                    tracks_list.append((idx, assigned_track))

            # assign tracks back to dataframe
            for idx, track in tracks_list:
                df.at[idx, "track"] = track

            # create y_axis combining group and track
            df["y_axis"] = (
                df["group"].astype(str) + " [" + df["track"].astype(str) + "]"
            )

        if df.empty:
            st.info('No events to display for "%s"' % header)
            continue

        fig = px.timeline(
            df,
            x_start="start",
            x_end="end",
            y="y_axis",
            color="group",
            text="title",
            title=header,
        )
        fig.update_traces(
            textposition="inside", marker=dict(line=dict(color="black", width=1))
        )
        fig.update_layout(
            height=len(df) * 40 + 200,
            yaxis_title="",
            legend_title="Group",
            xaxis=dict(rangeslider=dict(visible=True), type="date"),
        )
        st.plotly_chart(fig, use_container_width=True)
# (No global timeline render at bottom; per-header tabs already render timelines.)
