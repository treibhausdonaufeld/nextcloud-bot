"""Group org chart (vis-network) and group/member detail views."""

import logging

from ravyn import JSONResponse, Request, Template, get

from app.i18n import template_context
from app.models import Group, Mention, NCUserList
from app.settings import user_regex

logger = logging.getLogger(__name__)


def top_group_name() -> str:
    from app.services.config import bot_config

    try:
        return bot_config.organisation.top_group_name
    except Exception:  # bot config page not available
        return "Koordinationskreis"


def build_group_graph(
    all_groups: list[Group],
    user_list: NCUserList,
    with_members: bool,
    with_subgroups: bool,
    limit_group: str,
    limit_user: str,
) -> tuple[list[dict], list[dict]]:
    nodes: list[dict] = []
    edges: list[dict] = []

    def add_members(group: Group, level: int) -> None:
        members = [m for m in group.all_members if not limit_user or m == limit_user]
        for member_name in members:
            user = user_list.get_user_by_uid(member_name)
            if not user:
                continue

            member_id = f"{group.name}:{member_name}"

            if member_name in group.coordination:
                color = "#FF5733"  # Red for coordination
            elif member_name in group.delegate:
                color = "#33C1FF"  # Blue for delegates
            else:
                color = "#DAA520"  # Goldenrod for regular members

            nodes.append(
                {
                    "id": member_id,
                    "label": str(user),
                    "size": 10,
                    "color": color,
                    "title": member_name,
                    "level": level,
                    "shape": "dot",
                }
            )
            edges.append({"from": group.name, "to": member_id})

    top_group = None
    top_level_groups: list[Group] = []
    if limit_group:
        top_group = next((g for g in all_groups if g.name == limit_group), None)
    else:
        top_group = next(
            (g for g in all_groups if g.name == top_group_name()),
            None,
        )
        top_level_groups = sorted(
            [
                g
                for g in all_groups
                if not g.parent_group and g is not top_group and g.name != "Großgruppe"
            ]
        )

    if not top_group:
        return [], []

    if not limit_user or limit_user in top_group.all_members:
        nodes.append(
            {
                "id": top_group.name,
                "label": f"{top_group.abbreviated} ({len(top_group.all_members)})",
                "size": 60,
                "color": "#2FA24E",
                "shape": "box",
                "level": 1,
            }
        )
    for g in top_level_groups:
        if limit_user and limit_user not in g.all_members:
            continue
        nodes.append(
            {
                "id": g.name,
                "shape": "box",
                "label": f"{g.abbreviated} ({len(g.all_members)})",
                "size": 40,
                "color": "#608FFD",
                "level": 2,
            }
        )
    edges.extend({"from": top_group.name, "to": g.name} for g in top_level_groups)

    for group in top_level_groups + [top_group]:
        subgroups = [cg for cg in all_groups if cg.parent_group == group.name]

        # Filter subgroups if limit_user is set
        if limit_user:
            subgroups = [cg for cg in subgroups if limit_user in cg.all_members]

        if with_members:
            add_members(group, level=3)

        if not with_subgroups:
            continue

        for subgroup in subgroups:
            nodes.append(
                {
                    "id": subgroup.name,
                    "label": f"{subgroup.abbreviated} ({len(subgroup.all_members)})",
                    "size": 20,
                    "color": "#993699",
                    "level": 3,
                    "shape": "dot",
                }
            )
            edges.append({"from": group.name, "to": subgroup.name})

            if with_members:
                add_members(subgroup, level=4)

    return nodes, edges


def user_detail_context(username: str, all_groups: list[Group]) -> dict:
    """Details for one user: roles in groups + pages mentioning them."""
    user_list = NCUserList()
    user = user_list.get_user_by_uid(username)

    member_of_groups = [
        {
            "name": g.name,
            "coordination": username in g.coordination,
            "delegate": username in g.delegate,
        }
        for g in all_groups
        if username in g.all_members
    ]

    mention_pages = []
    for row in Mention.pages_for_user(username):
        try:
            if Group.valid_group_names(row["title"]):
                continue  # filter out group pages
        except Exception:  # bot config unavailable
            pass
        mention_pages.append(row)

    from app.models import CollectivePage

    pages_with_lines = []
    mention_str = f"mention://user/{username}"
    for row in mention_pages:
        page = CollectivePage.get_from_page_id_or_none(row["page_id"])
        if not page or not page.content:
            continue
        lines = [
            line.strip()
            for line in page.content.splitlines()
            if mention_str in line and user_regex.search(line)
        ]
        pages_with_lines.append({"page": page, "lines": lines})

    return {
        "user": user,
        "username": username,
        "member_of_groups": member_of_groups,
        "mention_pages": pages_with_lines,
    }


def _checkbox(request: Request, name: str, default: bool) -> bool:
    """Read a checkbox value; unchecked boxes are absent from the query, so
    defaults only apply before the form was submitted at all."""
    if request.query_params.get("submitted") != "1":
        return default
    return request.query_params.get(name) == "true"


@get("/groups")
def groups_page(
    request: Request,
    limit_group: str = "",
    limit_user: str = "",
    solver: str = "forceAtlas2Based",
    height: int = 700,
) -> Template:
    hierarchical = _checkbox(request, "hierarchical", False)
    with_members = _checkbox(request, "with_members", True)
    with_subgroups = _checkbox(request, "with_subgroups", True)
    user_list = NCUserList()
    all_groups = sorted(Group.fetch(limit=1000))

    # Only association members are offered in the picker (see
    # `NCUserList.is_member`); the graph itself still shows every person a
    # group page lists.
    users = [
        {"username": u.username, "displayname": u.displayname or u.username}
        for u in sorted(user_list.get_member_users(), key=lambda u: u.displayname or "")
    ]

    return Template(
        name="groups.html",
        context=template_context(
            request,
            all_groups=all_groups,
            users=users,
            hierarchical=hierarchical,
            with_members=with_members,
            with_subgroups=with_subgroups,
            limit_group=limit_group,
            limit_user=limit_user,
            solver=solver,
            height=max(300, min(height, 1200)),
        ),
    )


@get("/groups/graph.json")
def groups_graph(
    request: Request,
    with_members: bool = True,
    with_subgroups: bool = True,
    limit_group: str = "",
    limit_user: str = "",
) -> JSONResponse:
    user_list = NCUserList()
    all_groups = Group.fetch(limit=1000)
    nodes, edges = build_group_graph(
        all_groups, user_list, with_members, with_subgroups, limit_group, limit_user
    )
    return JSONResponse({"nodes": nodes, "edges": edges})


@get("/groups/detail")
def group_detail(request: Request, node: str = "") -> Template:
    all_groups = Group.fetch(limit=1000)
    context = template_context(request, node=node)

    group = None
    try:
        group = Group.get_by_name(node)
    except ValueError:
        pass

    if group:
        subgroups = sorted([cg for cg in all_groups if cg.parent_group == group.name])
        user_list = NCUserList()

        def names(user_ids: list[str]) -> list[str]:
            result = []
            for user_id in user_ids:
                user = user_list.get_user_by_uid(user_id)
                result.append(user.displayname if user else user_id)
            return result

        context.update(
            group=group,
            subgroups=subgroups,
            coordination_names=names(group.coordination),
            delegate_names=names(group.delegate),
            member_names=names(group.members),
        )
        return Template(name="partials/group_detail.html", context=context)

    # person selected, show user details
    username = node.split(":")[-1]
    context.update(user_detail_context(username, all_groups))
    return Template(name="partials/user_detail.html", context=context)
