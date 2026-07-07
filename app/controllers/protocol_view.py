"""In-app protocol viewer: popup with rendered markdown and version history.

Protocols are shown from the bot's own database (see
`app.models.protocol_version`). The app never writes back to Nextcloud —
every version's raw markdown can be displayed and copied so an older
version can be restored manually in Nextcloud if needed.
"""

import logging

from ravyn import Request, Template, get
from ravyn.responses import Response

from app.i18n import activate, template_context
from app.models import CollectivePage, NCUserList, ProtocolMedia, ProtocolVersion
from app.services.protocol_media import sync_page_media
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


def _is_htmx_request(request: Request) -> bool:
    return request.headers.get("hx-request", "").lower() == "true"


@get("/protocols/{page_id}/view")
def protocol_view(request: Request, page_id: int, version: int = 0) -> Template:
    """Protocol view for the given (or latest) version.

    Directly navigating to this URL (a shared link, a bookmark, a fresh
    tab) renders a full standalone page — protocols are shareable by URL.
    When the request comes from htmx (opened from the protocols list or
    the logbook), only the popup partial is returned for the dialog swap.
    """
    # activate the request language before any _() call (template_context
    # only runs at the end of the handler)
    activate(request)

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

    # Likewise backfill attachments: pages synced before the media feature
    # existed only get their attachments during the next content change, so
    # fetch anything still missing when the protocol is actually viewed.
    # (Idempotent and cheap when everything is already stored.)
    if page:
        try:
            sync_page_media(page)
        except Exception:
            logger.exception("Failed to backfill media for page %s", page_id)

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

    template_name = (
        "partials/protocol_view.html"
        if _is_htmx_request(request)
        else "protocol_page.html"
    )
    return Template(
        name=template_name,
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


@get("/protocols/{page_id}/media/{folder}/{name}")
def protocol_media(request: Request, page_id: int, folder: str, name: str) -> Response:
    """Serve a stored attachment ("<folder-id>/<filename>", see media_name)."""
    media = ProtocolMedia.get_for_page(page_id, f"{folder}/{name}")
    if media is None:
        return Response(content=b"Not found", status_code=404, media_type="text/plain")
    data = media.read_file()
    if data is None:
        # the file was pruned from disk to free space
        return Response(content=b"Not found", status_code=404, media_type="text/plain")
    return Response(
        content=data,
        media_type=media.content_type or "application/octet-stream",
        headers={
            "cache-control": "public, max-age=86400",
            # attachments are user-controlled: forbid MIME sniffing and any
            # active content (e.g. scripts in SVG files)
            "x-content-type-options": "nosniff",
            "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'",
        },
    )
