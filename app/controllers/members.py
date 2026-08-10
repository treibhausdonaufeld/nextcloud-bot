"""Members: who currently holds which role in which group, plus role history.

The overview lists every member with the roles they hold right now (read from
the parsed `Group` pages, which are the source of truth). Clicking a member
opens their full role history, clicking a role badge lists everyone who holds
or held that role — both from the `GroupRole` history table.
"""

import logging

from ravyn import Request, Template, get

from app.controllers.logbook import group_hue
from app.i18n import activate, template_context
from app.models import Group, GroupRole, NCUserList
from app.models.base import format_date
from app.models.group_role import ROLE_FIELDS, ROLES
from app.settings import _

logger = logging.getLogger(__name__)


def role_label(role: str) -> str:
    """Translated label for a role key."""
    labels = {
        "coordination": _("Coordination"),
        "delegate": _("Delegate"),
        "member": _("Member"),
    }
    return labels.get(role, role)


def role_options() -> list[dict]:
    return [{"key": role, "label": role_label(role)} for role in ROLES]


def current_assignments(groups: list[Group]) -> list[dict]:
    """Every role currently held, straight from the parsed group pages."""
    assignments = []
    for group in groups:
        for role, field in ROLE_FIELDS.items():
            for username in getattr(group, field, []) or []:
                assignments.append(
                    {
                        "username": username,
                        "group": group.name,
                        "page_id": group.page_id,
                        "role": role,
                    }
                )
    return assignments


def _decorate(assignment: dict, start: int | None) -> dict:
    """Add the display fields the templates need to a raw assignment."""
    return assignment | {
        "role_label": role_label(assignment["role"]),
        "hue": group_hue(assignment["group"]),
        "start": format_date(start) or "",
    }


def member_rows(
    role_filter: str = "", group_filter: str = "", query: str = ""
) -> list[dict]:
    """One row per member: their current roles and how much history they have.

    Members without any current role are listed too (with an empty role list)
    as long as they pass the filters, so the page really is an overview of all
    members.
    """
    user_list = NCUserList()
    groups = sorted(Group.all_cached())
    assignments = current_assignments(groups)
    starts = GroupRole.start_dates_by_key()

    history = GroupRole.all_rows()
    past_counts: dict[str, int] = {}
    for row in history:
        if row.end_date is not None:
            past_counts[row.username] = past_counts.get(row.username, 0) + 1

    current_by_user: dict[str, list[dict]] = {}
    for assignment in assignments:
        key = (assignment["page_id"], assignment["username"], assignment["role"])
        current_by_user.setdefault(assignment["username"], []).append(
            _decorate(assignment, starts.get(key))
        )

    enabled = {u.username for u in user_list.get_enabled_users()}
    # Former members keep showing up as long as they appear in the history.
    usernames = enabled | set(current_by_user) | {r.username for r in history}

    rows: list[dict] = []
    for username in usernames:
        user = user_list.get_user_by_uid(username)
        roles = sorted(
            current_by_user.get(username, []),
            key=lambda r: (list(ROLES).index(r["role"]), r["group"]),
        )

        if role_filter and not any(r["role"] == role_filter for r in roles):
            continue
        if group_filter and not any(r["group"] == group_filter for r in roles):
            continue

        displayname = (user.displayname if user else "") or username
        if query and query.lower() not in displayname.lower():
            continue

        rows.append(
            {
                "username": username,
                "displayname": displayname,
                "active": username in enabled,
                "roles": roles,
                "past_count": past_counts.get(username, 0),
            }
        )

    rows.sort(key=lambda row: (not row["active"], str(row["displayname"]).lower()))
    return rows


def member_history(username: str) -> tuple[list[dict], list[dict]]:
    """Current and past roles of one member, both newest first."""
    groups = {g.page_id: g for g in Group.all_cached()}
    current: list[dict] = []
    past: list[dict] = []
    for row in GroupRole.for_user(username):
        group = groups.get(row.page_id)
        entry = {
            "group": group.name if group else row.group_name,
            "page_id": row.page_id,
            "role": row.role,
            "role_label": role_label(row.role),
            "hue": group_hue(group.name if group else row.group_name),
            "start": row.start_display,
            "end": row.end_display,
        }
        (current if row.is_current else past).append(entry)
    past.sort(key=lambda entry: entry["end"] or "", reverse=True)
    return current, past


def role_holders(role: str, group_name: str = "") -> tuple[list[dict], list[dict]]:
    """Members who hold (current) and held (past) a role, newest first."""
    user_list = NCUserList()
    current: list[dict] = []
    past: list[dict] = []
    for row in GroupRole.for_role(role, group_name):
        user = user_list.get_user_by_uid(row.username)
        entry = {
            "username": row.username,
            "displayname": (user.displayname if user else "") or row.username,
            "group": row.group_name,
            "hue": group_hue(row.group_name),
            "start": row.start_display,
            "end": row.end_display,
        }
        (current if row.is_current else past).append(entry)
    current.sort(key=lambda entry: (entry["group"], entry["displayname"].lower()))
    past.sort(key=lambda entry: entry["end"] or "", reverse=True)
    return current, past


@get("/members")
def members_page(
    request: Request,
    role: str = "",
    group: str = "",
    q: str = "",
) -> Template:
    # Role labels are translated while collecting the rows, so the request
    # language has to be active before that (template_context activates it
    # only when the context is built).
    activate(request)

    if role not in ROLES:
        role = ""

    rows = member_rows(role_filter=role, group_filter=group, query=q.strip())
    group_names = sorted({g.name for g in Group.all_cached()})

    return Template(
        name="members.html",
        context=template_context(
            request,
            members=rows,
            group_names=group_names,
            role_options=role_options(),
            selected_role=role,
            selected_group=group,
            query=q.strip(),
        ),
    )


@get("/members/user/{username}")
def member_detail(request: Request, username: str) -> Template:
    activate(request)
    user_list = NCUserList()
    user = user_list.get_user_by_uid(username)
    current, past = member_history(username)

    return Template(
        name="partials/member_detail.html",
        context=template_context(
            request,
            username=username,
            displayname=(user.displayname if user else "") or username,
            current_roles=current,
            past_roles=past,
        ),
    )


@get("/members/role/{role}")
def role_detail(request: Request, role: str, group: str = "") -> Template:
    activate(request)
    current, past = ([], []) if role not in ROLES else role_holders(role, group)

    return Template(
        name="partials/role_detail.html",
        context=template_context(
            request,
            role=role,
            role_label=role_label(role) if role in ROLES else role,
            group_name=group,
            hue=group_hue(group),
            current_holders=current,
            past_holders=past,
        ),
    )
