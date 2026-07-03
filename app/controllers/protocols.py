"""Protocols: table with group filter, full-text search and member statistics."""

import logging
from datetime import datetime

from ravyn import Request, Template, get

from app.db import search
from app.i18n import template_context
from app.models import CollectivePage, Group, NCUserList, Protocol

logger = logging.getLogger(__name__)


def display_users(user_list: NCUserList, user_ids: list[str]) -> str:
    names = []
    for user_id in user_ids:
        try:
            names.append(str(user_list[user_id]))
        except KeyError:
            names.append(user_id)
    return ", ".join(names)


def group_name_map() -> dict[int | None, str]:
    return {g.page_id: g.name for g in Group.all_cached()}


def member_statistics(
    group_name: str, protocols: list[Protocol], group_names: dict[int | None, str]
) -> list[dict]:
    """Moderation/protocol/attendance counts per member of the group."""
    try:
        group = Group.get_by_name(group_name)
        all_group_members = group.all_members
    except ValueError:
        all_group_members = []

    group_protocols = [
        p for p in protocols if group_names.get(p.group_page_id) == group_name
    ]

    user_stats = {
        user_id: {"moderated": 0, "protocol": 0, "attended": 0}
        for user_id in all_group_members
    }

    for protocol in group_protocols:
        for user_id in protocol.moderated_by:
            if user_id in user_stats:
                user_stats[user_id]["moderated"] += 1
        for user_id in protocol.protocol_by:
            if user_id in user_stats:
                user_stats[user_id]["protocol"] += 1
        for user_id in protocol.participants:
            if user_id in user_stats:
                user_stats[user_id]["attended"] += 1

    user_list = NCUserList()
    member_data = []
    for user_id, stats in user_stats.items():
        try:
            user_name = str(user_list[user_id])
        except KeyError:
            user_name = user_id
        member_data.append({"name": user_name, **stats})
    return member_data


@get("/protocols")
def protocols_page(request: Request, group: str = "", q: str = "") -> Template:
    user_list = NCUserList()
    all_protocols = Protocol.fetch(limit=10000)
    group_names = group_name_map()

    group_options = sorted(
        {group_names.get(p.group_page_id, "") for p in all_protocols} - {""}
    )

    # filter out protocols in the future
    now_str = datetime.now().strftime("%Y-%m-%d")

    if q.strip():
        # full-text search over protocol pages, then map hits back to protocols
        hits = search(q, doc_types=["page"], limit=50)
        hit_page_ids = {int(row["doc_id"]) for row in hits}
        protocols = [p for p in all_protocols if p.page_id in hit_page_ids]
        if group:
            protocols = [
                p for p in protocols if group_names.get(p.group_page_id) == group
            ]
    elif group:
        protocols = sorted(
            [p for p in all_protocols if group_names.get(p.group_page_id) == group],
            key=lambda p: p.date,
            reverse=True,
        )
    else:
        protocols = sorted(
            [p for p in all_protocols if p.date <= now_str],
            key=lambda p: p.date,
            reverse=True,
        )

    rows = []
    for protocol in protocols:
        page = CollectivePage.get_from_page_id_or_none(protocol.page_id)
        rows.append(
            {
                "date": protocol.date,
                "title": page.title if page else "",
                "url": (page.url if page else "") or "",
                "group": group_names.get(protocol.group_page_id, ""),
                "moderated_by": display_users(user_list, protocol.moderated_by),
                "protocol_by": display_users(user_list, protocol.protocol_by),
                "participants": display_users(user_list, protocol.participants),
            }
        )

    member_data = member_statistics(group, all_protocols, group_names) if group else []

    return Template(
        name="protocols.html",
        context=template_context(
            request,
            rows=rows,
            group_options=group_options,
            selected_group=group,
            q=q,
            member_data=member_data,
        ),
    )
