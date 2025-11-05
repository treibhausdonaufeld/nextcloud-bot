from typing import Any, Hashable, Tuple

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
        # check if end column exists and has any non-empty values
        has_end_column = "End" in rows[0].keys()

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
            if has_end_column:
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

            if has_end_column:
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
                    df.at[idx, "track"] = str(int(track + 1))

                # create y_axis combining group and track
                df["y_axis"] = (
                    df["group"].astype(str) + " [" + df["track"].astype(str) + "]"
                )

                # order y_axis categories alphabetically to ensure proper display order
                y_order = sorted(df["y_axis"].unique(), key=lambda s: str(s).lower())
                df["y_axis"] = pd.Categorical(
                    df["y_axis"], categories=y_order, ordered=True
                )
            else:
                # for events without end dates, use group directly as y_axis
                df["y_axis"] = df["group"].astype(str)
                y_order = sorted(df["y_axis"].unique(), key=lambda s: str(s).lower())
                df["y_axis"] = pd.Categorical(
                    df["y_axis"], categories=y_order, ordered=True
                )

        if df.empty:
            st.info('No events to display for "%s"' % header)
            continue

        if has_end_column:
            # render as timeline with bars
            fig = px.timeline(
                df,
                x_start="start",
                x_end="end",
                y="y_axis",
                color="group",
                text="title",
                hover_name="title",
                title=header,
                category_orders={"y_axis": y_order},
            )
            fig.update_traces(
                textposition="inside",
                textfont=dict(size=12),
                marker=dict(line=dict(color="gray", width=0.4)),
            )
            fig.update_traces(insidetextanchor="middle")
            fig.update_layout(
                height=len(df) * 25 + 100,
                yaxis_title="",
                legend_title="Group",
                xaxis=dict(
                    rangeslider=dict(visible=True),
                    type="date",
                ),
            )

        else:
            # render as scatter plot with big dots for point events
            # To avoid overlapping labels, convert y_axis categories to numeric
            # base positions and add small offsets for duplicate (group, date)
            # pairs. We will plot markers at the numeric positions and add
            # rotated annotations at the same coordinates.
            # Ignore groups completely for scatter-only events: use a shared
            # baseline at 0 for all milestones. Compute offsets across the
            # whole dataframe for events within 7 days.
            df["y_base"] = 0.0

            # Cluster events globally by date (sorted) where cluster span <= 7 days
            offset_map = {}
            gr = df.sort_values("start")
            cluster: list[Tuple[Hashable, Any]] = []
            cluster_min = None
            for idx, row in gr.iterrows():
                s = row["start"]
                if not cluster:
                    cluster = [(idx, s)]
                    cluster_min = s
                else:
                    if (s - cluster_min) <= pd.Timedelta(days=7):
                        cluster.append((idx, s))
                    else:
                        # assign offsets for existing cluster
                        n = len(cluster)
                        if n == 1:
                            offset_map[cluster[0][0]] = 0.0
                        else:
                            span = 0.6
                            step = span / max(n - 1, 1)
                            start_off = -span / 2
                            for i, (cidx, _) in enumerate(cluster):
                                offset_map[cidx] = start_off + i * step
                        cluster = [(idx, s)]
                        cluster_min = s
            # finalize last cluster
            if cluster:
                n = len(cluster)
                if n == 1:
                    offset_map[cluster[0][0]] = 0.0
                else:
                    span = 0.6
                    step = span / max(n - 1, 1)
                    start_off = -span / 2
                    for i, (cidx, _) in enumerate(cluster):
                        offset_map[cidx] = start_off + i * step

            # apply offsets to build final numeric y positions around 0
            df["y_pos"] = df.index.map(lambda idx: 0.0 + offset_map.get(idx, 0.0))

            # build scatter with numeric y positions (no color/group)
            fig = px.scatter(
                df,
                x="start",
                y="y_pos",
                hover_name="title",
                title=header,
            )
            fig.update_traces(
                marker=dict(size=15, line=dict(color="gray", width=1)),
                mode="markers",
            )

            # fix y-axis to -5..5 and hide y-axis lines/grid so the center
            # baseline is visually prominent. Tick labels are not needed.
            fig.update_yaxes(
                range=[-5, 5],
                autorange=False,
                showticklabels=False,
                showgrid=False,
                zeroline=False,
                showline=False,
            )
            # (no baseline line drawn for scatter-only charts)

            # Add rotated text annotations at the adjusted positions
            for idx, row in df.iterrows():
                fig.add_annotation(
                    x=row["start"],
                    y=row["y_pos"],
                    text=row["title"],
                    textangle=-45,
                    showarrow=False,
                    xanchor="left",
                    yanchor="bottom",
                    font=dict(size=12),
                )

            # set default view range to 1 year window for scatter-only charts
            min_date = df["start"].max() - pd.DateOffset(years=1)
            max_date = df["start"].max() + pd.DateOffset(months=2)
            fig.update_xaxes(range=[min_date, max_date])
            # show vertical grid lines aligned with x-axis tick labels
            fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor="lightgray")

            # make pan the default interaction mode for scatter plots
            fig.update_layout(dragmode="pan", height=600)

        st.plotly_chart(fig, use_container_width=True)
# (No global timeline render at bottom; per-header tabs already render timelines.)
