"""People overview and person detail views."""

import logging
from datetime import datetime

from ravyn import Request, Template, get

from app.i18n import template_context
from app.models import Group, MemberHistory, NCUserList

logger = logging.getLogger(__name__)

ROLE_LABELS = {
    "coordination": "Coordination",
    "delegate": "Delegate",
    "member": "Member",
    "absent": "Absent",
}

ROLE_ORDER = {"coordination": 0, "delegate": 1, "member": 2, "absent": 3}


@get("/people")
def people_page(request: Request) -> Template:
    user_list = NCUserList()
    all_groups = {g.page_id: g for g in Group.fetch(limit=1000)}
    active_rows = MemberHistory.fetch(active=True, limit=100000)

    people: dict[str, list[dict]] = {}
    for row in active_rows:
        group = all_groups.get(row.group_page_id)
        if group is None:
            continue
        display_name = user_list.display_name(row.username)
        people.setdefault(row.username, []).append(
            {
                "display_name": display_name,
                "group_name": group.name,
                "group_emoji": group.emoji or "",
                "role": row.role,
                "role_label": ROLE_LABELS.get(row.role, row.role),
                "role_order": ROLE_ORDER.get(row.role, 99),
                "since": datetime.fromtimestamp(row.first_seen).strftime("%Y-%m-%d"),
            }
        )

    people_list = []
    for username in sorted(
        people.keys(), key=lambda u: people[u][0]["display_name"].lower()
    ):
        assignments = people[username]
        coordination = [a for a in assignments if a["role"] == "coordination"]
        delegate = [a for a in assignments if a["role"] == "delegate"]
        member = [a for a in assignments if a["role"] == "member"]
        absent = [a for a in assignments if a["role"] == "absent"]
        priority = sorted(
            coordination + delegate + member + absent, key=lambda a: a["role_order"]
        )

        people_list.append(
            {
                "username": username,
                "display_name": priority[0]["display_name"] if priority else username,
                "assignments": priority,
                "coordination_count": len(coordination),
                "delegate_count": len(delegate),
                "member_count": len(member),
                "absent_count": len(absent),
            }
        )

    return Template(
        name="people.html",
        context=template_context(request, people=people_list),
    )


@get("/people/detail")
def person_detail(request: Request, username: str = "") -> Template:
    if not username:
        return Template(
            name="partials/person_detail.html", context=template_context(request)
        )

    user_list = NCUserList()
    all_groups = {g.page_id: g for g in Group.fetch(limit=1000)}
    rows = MemberHistory.fetch(username=username, limit=10000, order_by="-first_seen")
    # newest first is default; reverse so the timeline reads top → bottom
    rows = list(reversed(rows))

    display_name = user_list.display_name(username)

    history: list[dict] = []
    for row in rows:
        group = all_groups.get(row.group_page_id)
        group_name = group.name if group else row.group_name
        group_emoji = group.emoji if group else ""

        duration_days = int((row.last_seen - row.first_seen) / 86400)
        duration_str = f"{duration_days} days" if duration_days > 0 else "< 1 day"

        history.append(
            {
                "group_name": group_name,
                "group_emoji": group_emoji,
                "role": row.role,
                "role_label": ROLE_LABELS.get(row.role, row.role),
                "first_seen": datetime.fromtimestamp(row.first_seen).strftime(
                    "%Y-%m-%d"
                ),
                "last_seen": datetime.fromtimestamp(row.last_seen).strftime("%Y-%m-%d"),
                "duration": duration_str,
                "active": row.active,
            }
        )

    return Template(
        name="partials/person_detail.html",
        context=template_context(
            request,
            username=username,
            display_name=display_name,
            history=history,
            user=user_list.get_user_by_uid(username),
        ),
    )
