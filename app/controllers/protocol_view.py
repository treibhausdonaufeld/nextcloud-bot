"""In-app protocol viewer: popup with rendered markdown and version history.

Protocols are shown from the bot's own database (see
`app.models.protocol_version`). The app never writes back to Nextcloud —
every version's raw markdown can be displayed and copied so an older
version can be restored manually in Nextcloud if needed.
"""

import logging

from ravyn import Request, Template, get
from ravyn.responses import Response

from app.i18n import template_context
from app.models import CollectivePage, NCUserList, ProtocolMedia, ProtocolVersion
from app.services.protocol_render import render_diff_html, render_protocol_html
from app.settings import _

logger = logging.getLogger(__name__)


def _display_name(user_list: NCUserList, username: str | None) -> str:
    if not username:
        return _("unknown")
    try:
        return str(user_list[username])
    except KeyError:
        return username


def _user_name_map(user_list: NCUserList) -> dict[str, str]:
    try:
        return {
            u.username: (u.displayname or u.username)
            for u in user_list.get_enabled_users()
        }
    except Exception:
        return {}


@get("/protocols/{page_id}/view")
def protocol_view(request: Request, page_id: int, version: int = 0) -> Template:
    """Protocol popup partial for the given (or latest) version."""
    page = CollectivePage.get_from_page_id_or_none(page_id)
    versions = ProtocolVersion.history_for_page(page_id) if page else []

    # Lazily backfill a first version for pages stored before the versioning
    # feature existed, so editor tracking starts from here on.
    if page and not versions:
        try:
            recorded = ProtocolVersion.record(page)
            if recorded is not None:
                versions = [recorded]
        except Exception:
            logger.exception("Failed to backfill version for page %s", page_id)

    user_list = NCUserList()
    latest_no = versions[0].version if versions else None
    selected = None
    if versions:
        selected = next((v for v in versions if v.version == version), versions[0])

    content = selected.content if selected else (page.content if page else "")
    content_html = render_protocol_html(
        content, page_id, user_names=_user_name_map(user_list)
    )

    version_items = [
        {
            "version": v.version,
            "editor": _display_name(user_list, v.editor),
            "timestamp": v.formatted_timestamp,
            "is_latest": v.version == latest_no,
            "is_selected": selected is not None and v.version == selected.version,
        }
        for v in versions
    ]

    return Template(
        name="partials/protocol_view.html",
        context=template_context(
            request,
            page=page,
            content_html=content_html,
            raw_content=content or "",
            versions=version_items,
            selected_version=selected.version if selected else None,
            selected_is_latest=(selected is None or selected.version == latest_no),
            selected_editor=_display_name(user_list, selected.editor)
            if selected
            else None,
            selected_timestamp=selected.formatted_timestamp if selected else None,
            diff_html=render_diff_html(selected.diff) if selected else "",
        ),
    )


@get("/protocols/{page_id}/media/{name}")
def protocol_media(request: Request, page_id: int, name: str) -> Response:
    media = ProtocolMedia.get_for_page(page_id, name)
    if media is None:
        return Response(content=b"Not found", status_code=404, media_type="text/plain")
    return Response(
        content=bytes(media.data),
        media_type=media.content_type or "application/octet-stream",
        headers={"cache-control": "public, max-age=86400"},
    )
