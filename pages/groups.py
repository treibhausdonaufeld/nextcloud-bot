from gettext import gettext as _
from typing import cast

import streamlit as st
from streamlit_agraph import Config, Edge, Node, agraph

from lib.couchdb import couchdb
from lib.menu import menu
from lib.nextcloud.models.collective_page import CollectivePage
from lib.nextcloud.models.group import Group
from lib.nextcloud.models.user import NCUserList
from lib.settings import settings
from lib.streamlit_oauth import load_user_data


def display_users(title: str, user_ids: list[str]):
    if user_ids:
        st.write(f"**{title}:**")
        for user_id in user_ids:
            user = user_list.get_user_by_uid(user_id)
            displayname = user_id
            if user and user.ocs and user.ocs.displayname:
                displayname = user.ocs.displayname
            st.write(f"- {displayname}")


def display_group(
    group: Group, user_list: NCUserList, all_groups: list[Group], level: int = 0
):
    """Display a group and its children."""
    # with st.expander(f"{'➡️' * level} {group.name}"):
    if group.parent_group:
        st.write(f"**{_('Parent Group')}:** {group.parent_group}")

    if group.short_names:
        st.write(f"**{_('Short Names')}:** {', '.join(group.short_names)}")

    cols = st.columns(3)
    with cols[0]:
        display_users(_("Coordination"), group.coordination)
    with cols[1]:
        display_users(_("Delegates"), group.delegate)
    with cols[2]:
        display_users(_("Members"), group.members)

    # children = [g for g in all_groups if g.parent_group == group.name]
    # for child in children:
    #     with st.expander(f"{'➡️' * (level + 1)} {child.name}"):
    #         display_group(child, user_list, all_groups, level + 1)


def add_members(group: Group, nodes: list[Node], edges: list[Edge]) -> None:
    for member_name in group.all_members:
        member_id = f"{group.name}:{member_name}"

        if member_name in group.coordination:
            color = "#FF5733"  # Red for coordination
        elif member_name in group.delegate:
            color = "#33C1FF"  # Blue for delegates
        else:
            color = "#DAA520"  # Goldenrod for regular members

        nodes.append(
            Node(
                id=member_id,
                label=str(user_list[member_name]),
                size=10,
                color=color,
                title=member_name,
            )
        )
        edges.append(Edge(source=group.name, target=member_id, type="CURVE_SMOOTH"))


# Streamlit app starts here
title = _("Groups").format(common_name=settings.name)
st.set_page_config(page_title=title, page_icon="⭕", layout="wide")

menu()
load_user_data()

db = couchdb()

user_list = NCUserList()

st.title(title)


all_groups = cast(list[Group], Group.get_all())

# großgruppe as top_group
# top_group = next(
#     (g for g in all_groups if len(g.all_members) == 0 and not g.parent_group), None
# )
# koordinationskreis as top_group
top_group = next((g for g in all_groups if g.name == "Koordinationskreis"))

if not top_group:
    st.warning(_("No top-level group without members found for visualization"))
    st.stop()

top_level_groups = sorted(
    [
        g
        for g in all_groups
        if not g.parent_group and not g == top_group and not g.name == "Großgruppe"
    ]
)

cols = st.columns(4)
hierarchical = cols[0].checkbox(_("Hierarchical layout"), value=False)
with_members = cols[1].checkbox(_("With Members"), value=True)
with_subgroups = cols[2].checkbox(_("With Subgroups"), value=True)

nodes = [
    Node(
        id=top_group.name,
        label=f"{top_group} ({len(top_group.all_members)})",
        size=60,
        color="#006B1D",
        shape="box",
    )
] + [
    Node(
        id=g.name,
        shape="box",
        label=f"{g}({len(g.all_members)})",
        size=40,
        color="#608FFD",
    )
    for g in top_level_groups
]
edges = [
    Edge(source=top_group.name, target=g.name, type="CURVE_SMOOTH")
    for g in top_level_groups
]

for group in top_level_groups:
    subgroups = [cg for cg in all_groups if cg.parent_group == group.name]

    if with_members:
        add_members(group, nodes, edges)

    if not with_subgroups:
        continue

    for subgroup in subgroups:
        nodes.append(
            Node(
                id=subgroup.name,
                label=f"{subgroup} ({len(subgroup.all_members)})",
                size=20,
                color="#993699",
            )
        )
        edges.append(Edge(source=group.name, target=subgroup.name, type="CURVE_SMOOTH"))

        if with_members:
            add_members(subgroup, nodes, edges)

config = Config(
    width=1000,
    height=500,
    directed=False,
    nodeHighlightBehavior=True,
    highlightColor="#BE3230",
    hierarchical=hierarchical,
    physics=not hierarchical,
    collapsible=True,
    # nodeSpacing=400,
    # treeSpacing=400,
    node={"labelProperty": "label"},
    link={"labelProperty": "label", "renderLabel": True},
)

selected_node = agraph(nodes=nodes, edges=edges, config=config)

# for group in top_level_groups:
#     display_group(group, user_list, all_groups)

if selected_node:
    try:
        group = Group.get_by_name(selected_node)
        parent = f"{group.parent_group} :arrow_right: " if group.parent_group else ""
        st.write(f"### {parent}{selected_node}")

        display_group(group, user_list, all_groups)
    except ValueError:
        # person selected show some details
        member_name = selected_node.split(":")[-1]
        user = user_list[member_name]

        st.write(f"### {user.ocs.displayname}")

        st.write("#### " + _("Roles in Groups"))
        member_of_groups = [g for g in all_groups if member_name in g.all_members]
        for group in member_of_groups:
            role = (
                _("(Coordination)")
                if member_name in group.coordination
                else _("(Delegate)")
                if member_name in group.delegate
                else ""
            )
            st.write(f"- {group.name} {role}")

        st.write("#### " + _("Pages mentioning User"))
        user_view_result = db.query(
            "mentions/by_user", key=user.username, reduce=False, include_docs=True
        )

        pages = {CollectivePage(**row["doc"]) for row in user_view_result}

        # filter out group pages
        pages = {p for p in pages if not Group.valid_group_names(p.title)}

        st.write(_("Total Mentions: {count}").format(count=len(pages)))

        for page in sorted(pages, key=lambda p: p.ocs.timestamp or 0, reverse=True):
            if not page.content:
                continue

            for line in page.content.splitlines():
                if user.mention in line:
                    st.write(f"- **[{page.title}]({page.url})**: {line.strip()}")

            # st.markdown(f"- [{page.title}]({page.url})")
