"""Copy embedded media of protocol pages into the local database.

Collectives embeds attachments with relative markdown links pointing into a
``.attachments.<file-id>/`` folder next to the page's markdown file. This
module extracts those references, downloads the files via WebDAV and upserts
them as `ProtocolMedia` rows so the app can serve them itself.
"""

from __future__ import annotations

import logging
import mimetypes
import re
from typing import List
from urllib.parse import quote, unquote

import requests

from app.models.collective_page import CollectivePage
from app.models.protocol_media import ProtocolMedia
from app.settings import settings

logger = logging.getLogger(__name__)

# Matches the target of a markdown link/image pointing into an attachments
# folder: "(.attachments.2554464/photo.png)" (optionally "./"-prefixed,
# angle-bracketed or followed by a title).
ATTACHMENT_LINK_RE = re.compile(
    r"\(\s*<?((?:\./)?\.attachments\.\d+/[^)\s<>]+)>?(?:\s+\"[^\"]*\")?\s*\)"
)


def media_name(raw_path: str) -> str:
    """Stored/served name of an attachment: "<folder-id>/<filename>".

    Keeping the attachment folder id avoids collisions when a page
    references same-named files from different attachment folders (e.g.
    after copy-pasting content from another page).
    """
    path = unquote(raw_path)
    if path.startswith("./"):
        path = path[2:]
    return path.removeprefix(".attachments.")


def extract_attachment_paths(content: str) -> List[str]:
    """Return the unique attachment paths referenced in the markdown."""
    if not content:
        return []
    paths = []
    for match in ATTACHMENT_LINK_RE.finditer(content):
        path = match.group(1)
        if path.startswith("./"):
            path = path[2:]
        if path not in paths:
            paths.append(path)
    return paths


def _attachment_url(page: CollectivePage, relative_path: str) -> str:
    """WebDAV URL of an attachment, resolved relative to the page's folder."""
    base_str = str(settings.nextcloud.base_url).rstrip("/")
    filepath = "/".join(
        (page.collective_path or "", page.file_path or "", relative_path)
    )
    return (
        base_str
        + f"/remote.php/dav/files/{settings.nextcloud.admin_username}/"
        + quote(filepath.lstrip("/"), safe="/")
    )


def fetch_attachment(page: CollectivePage, relative_path: str) -> tuple[bytes, str]:
    """Download one attachment via WebDAV. Returns (data, content_type)."""
    url = _attachment_url(page, relative_path)
    logger.debug("Fetching attachment %s from %s", relative_path, url)
    resp = requests.get(
        url,
        auth=(settings.nextcloud.admin_username, settings.nextcloud.admin_password),
        timeout=90,
    )
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
    if not content_type or content_type == "application/octet-stream":
        guessed, _encoding = mimetypes.guess_type(relative_path)
        content_type = guessed or "application/octet-stream"
    return resp.content, content_type


def sync_page_media(page: CollectivePage) -> None:
    """Copy all referenced attachments of a page into the database.

    Already stored files are kept (attachment file names in Collectives are
    stable), so media referenced only by older versions stays available.
    """
    paths = extract_attachment_paths(page.content or "")
    if not paths:
        return

    existing = ProtocolMedia.names_for_page(page.page_id)

    for raw_path in paths:
        path = unquote(raw_path)
        name = media_name(raw_path)
        if name in existing:
            continue
        try:
            data, content_type = fetch_attachment(page, path)
        except Exception as e:
            logger.warning(
                "Failed to fetch attachment %s for page %s: %s",
                path,
                page.page_id,
                e,
            )
            continue

        media = ProtocolMedia(
            page_id=page.page_id,
            name=name,
            path=path,
            content_type=content_type,
            size=len(data),
            data=data,
        )
        media.store()
        existing.add(name)
        logger.info(
            "Stored attachment %s (%d bytes) for page %s", name, len(data), page.page_id
        )
