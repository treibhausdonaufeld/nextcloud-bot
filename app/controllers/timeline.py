"""Renders markdown tables from a Collectives page as Plotly charts.

The single Collectives page (``timeline_page_name``) holds two kinds of
sections: those with an *End* column become Gantt-style bars ("functions",
served at ``/functions``) and those without become point-event scatters
("milestones", served at ``/milestones``).
"""

import json
import logging

import pandas as pd
from ravyn import Request, Template, get

from app.i18n import template_context
from app.models import CollectivePage
from app.settings import _, settings

logger = logging.getLogger(__name__)


def parse_markdown_tables(md_text: str) -> dict[str, list[dict]]:
    """Parse headers and their following markdown tables into a dict:
    {header: [row_dict, ...], ...}
    """
    lines = md_text.splitlines()
    sections: dict[str, list[dict]] = {}
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


def _build_dataframe(rows: list[dict], header: str, has_end_column: bool):
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
    if df.empty:
        return df, []

    df["start"] = pd.to_datetime(df["start"], errors="coerce")
    if has_end_column:
        df["end"] = pd.to_datetime(df["end"], errors="coerce")
        # where end invalid, set to current date
        df.loc[df["end"].isna(), "end"] = pd.Timestamp.now()
    df = df.dropna(subset=["start"])

    # order rows by group (alphabetically) and then by start date
    group_order = sorted(df["group"].dropna().unique(), key=lambda s: str(s).lower())
    df["group"] = pd.Categorical(df["group"], categories=group_order, ordered=True)
    df = df.sort_values(["group", "start"])
    return df, group_order


def _assign_tracks(df: pd.DataFrame, group_order: list[str]) -> pd.DataFrame:
    """Assign track numbers within each group to prevent overlapping bars."""
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

    for idx, track in tracks_list:
        df.at[idx, "track"] = str(int(track + 1))

    df["y_axis"] = df["group"].astype(str) + " [" + df["track"].astype(str) + "]"
    return df


def _cluster_offsets(df: pd.DataFrame) -> dict:
    """Spread milestone labels that fall within 7 days of each other."""
    offset_map: dict = {}
    gr = df.sort_values("start")
    cluster: list = []
    cluster_min = None

    def finalize(cluster: list) -> None:
        n = len(cluster)
        if n == 1:
            offset_map[cluster[0][0]] = 0.0
        else:
            span = 0.6
            step = span / max(n - 1, 1)
            start_off = -span / 2
            for i, (cidx, _s) in enumerate(cluster):
                offset_map[cidx] = start_off + i * step

    for idx, row in gr.iterrows():
        s = row["start"]
        if not cluster:
            cluster = [(idx, s)]
            cluster_min = s
        elif (s - cluster_min) <= pd.Timedelta(days=7):
            cluster.append((idx, s))
        else:
            finalize(cluster)
            cluster = [(idx, s)]
            cluster_min = s
    if cluster:
        finalize(cluster)

    return offset_map


def build_timeline_figure(
    df: pd.DataFrame, group_order: list[str], header: str
) -> dict:
    """Gantt-style chart: one horizontal bar trace per group."""
    y_order = sorted(df["y_axis"].unique(), key=lambda s: str(s).lower())

    traces = []
    for group_name in group_order:
        gdf = df[df["group"].astype(str) == str(group_name)]
        if gdf.empty:
            continue
        traces.append(
            {
                "type": "bar",
                "orientation": "h",
                "name": str(group_name),
                "y": gdf["y_axis"].astype(str).tolist(),
                "base": gdf["start"].dt.strftime("%Y-%m-%dT%H:%M:%S").tolist(),
                "x": ((gdf["end"] - gdf["start"]).dt.total_seconds() * 1000).tolist(),
                "text": gdf["title"].tolist(),
                "textposition": "inside",
                "insidetextanchor": "middle",
                "textfont": {"size": 12},
                "marker": {"line": {"color": "gray", "width": 0.4}},
                "customdata": [
                    [s, e]
                    for s, e in zip(
                        gdf["start"].dt.strftime("%Y-%m-%d"),
                        gdf["end"].dt.strftime("%Y-%m-%d"),
                    )
                ],
                "hovertemplate": "<b>%{text}</b><br>%{customdata[0]} – %{customdata[1]}<extra></extra>",
            }
        )

    layout: dict = {
        # No chart title: the page heading already names the section, and the
        # top strip is given over to the horizontal legend below.
        "height": max(400, len(df) * 25 + 100),
        "barmode": "overlay",
        # Default to the pan/move tool rather than zoom-to-rectangle.
        "dragmode": "pan",
        # Horizontal legend above the plot so it doesn't eat horizontal space.
        "legend": {
            "title": {"text": "Group"},
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "left",
            "x": 0,
        },
        "margin": {"t": 40},
        "yaxis": {
            "title": "",
            "categoryorder": "array",
            "categoryarray": y_order,
            "autorange": "reversed",
            # Grow the left margin to fit the full row labels instead of clipping.
            "automargin": True,
        },
        "xaxis": {"type": "date", "rangeslider": {"visible": True}},
    }
    return {"data": traces, "layout": layout}


def build_milestone_figure(df: pd.DataFrame, header: str) -> dict:
    """Scatter chart with big dots and rotated labels for point events."""
    offset_map = _cluster_offsets(df)
    df = df.copy()
    df["y_pos"] = df.index.map(lambda idx: 0.0 + offset_map.get(idx, 0.0))

    trace = {
        "type": "scatter",
        "mode": "markers",
        "x": df["start"].dt.strftime("%Y-%m-%dT%H:%M:%S").tolist(),
        "y": df["y_pos"].tolist(),
        "hovertext": df["title"].tolist(),
        "hoverinfo": "text+x",
        "marker": {"size": 15, "line": {"color": "gray", "width": 1}},
    }

    annotations = [
        {
            "x": row["start"].strftime("%Y-%m-%dT%H:%M:%S"),
            "y": row["y_pos"],
            "text": row["title"],
            "textangle": -45,
            "showarrow": False,
            "xanchor": "left",
            "yanchor": "bottom",
            "font": {"size": 12},
        }
        for _idx, row in df.iterrows()
    ]

    # default view: 1 year window around the latest milestones
    min_date = df["start"].max() - pd.DateOffset(years=1)
    max_date = df["start"].max() + pd.DateOffset(months=2)

    layout: dict = {
        "title": {"text": header},
        "height": 600,
        "dragmode": "pan",
        "annotations": annotations,
        "yaxis": {
            "range": [-5, 5],
            "autorange": False,
            "showticklabels": False,
            "showgrid": False,
            "zeroline": False,
            "showline": False,
            "title": "",
        },
        "xaxis": {
            "type": "date",
            "range": [
                min_date.strftime("%Y-%m-%dT%H:%M:%S"),
                max_date.strftime("%Y-%m-%dT%H:%M:%S"),
            ],
            "showgrid": True,
            "gridwidth": 1,
            "gridcolor": "lightgray",
            "title": "",
        },
    }
    return {"data": [trace], "layout": layout}


def build_section_figures(md_text: str, kind: str | None = None) -> list[dict]:
    """One Plotly figure (as dict) per markdown section.

    ``kind`` filters the result: ``"function"`` keeps only sections with an End
    column (Gantt bars), ``"milestone"`` keeps only point-event sections
    (scatter), and ``None`` keeps every section. Empty sections cannot be
    classified, so they only surface in the unfiltered view.
    """
    sections = parse_markdown_tables(md_text)
    figures: list[dict] = []
    for header, rows in sections.items():
        if not rows:
            if kind is None:
                figures.append({"header": header, "figure": None, "kind": None})
            continue

        has_end_column = "End" in rows[0].keys()
        section_kind = "function" if has_end_column else "milestone"
        if kind is not None and section_kind != kind:
            continue

        df, group_order = _build_dataframe(rows, header, has_end_column)

        if df.empty:
            figures.append({"header": header, "figure": None, "kind": section_kind})
            continue

        if has_end_column:
            df = _assign_tracks(df, group_order)
            figure = build_timeline_figure(df, group_order, header)
        else:
            figure = build_milestone_figure(df, header)

        figures.append({"header": header, "figure": figure, "kind": section_kind})
    return figures


def _sections_context(
    request: Request, kind: str, page_title: str, empty_message: str
) -> dict:
    """Shared context for the /functions and /milestones pages."""
    context = template_context(request)

    try:
        page = CollectivePage.get_from_title(settings.nextcloud.timeline_page_name)
        md_text = page.content or ""
    except ValueError:
        md_text = ""

    context["page_title"] = page_title
    context["empty_message"] = empty_message
    context["sections"] = (
        build_section_figures(md_text, kind=kind) if md_text.strip() else []
    )
    # escape <, > and & so page-provided titles cannot break out of the
    # <script type="application/json"> block (XSS via "</script>")
    context["figures_json"] = (
        json.dumps(context["sections"])
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    return context


@get("/functions")
def functions_page(request: Request) -> Template:
    context = _sections_context(
        request,
        kind="function",
        page_title=_("Functions"),
        empty_message=_(
            "No function data found. Please create and populate the '%s' page "
            "in Nextcloud Collectives."
        )
        % settings.nextcloud.timeline_page_name,
    )
    return Template(name="sections.html", context=context)


@get("/milestones")
def milestones_page(request: Request) -> Template:
    context = _sections_context(
        request,
        kind="milestone",
        page_title=_("Milestones"),
        empty_message=_(
            "No milestone data found. Please create and populate the '%s' page "
            "in Nextcloud Collectives."
        )
        % settings.nextcloud.timeline_page_name,
    )
    return Template(name="sections.html", context=context)
