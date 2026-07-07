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
from datetime import date, datetime
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


def protocol_date(page: CollectivePage) -> date | None:
    """Original date of a protocol, best effort.

    Protocol pages are titled "YYYY-MM-DD Group", so the title is the most
    reliable source and is available before the protocol is parsed. Falls
    back to the parsed Protocol row, then to the page's modification
    timestamp.
    """
    if page.title:
        try:
            return datetime.strptime(page.title.split(" ")[0], "%Y-%m-%d").date()
        except ValueError:
            pass

    from app.models.protocol import Protocol

    protocol = Protocol.fetch_one(page_id=page.page_id)
    if protocol is not None:
        try:
            if protocol.date_obj is not None:
                return protocol.date_obj
        except ValueError:
            pass

    if page.timestamp:
        try:
            return datetime.fromtimestamp(float(page.timestamp)).date()
        except (TypeError, ValueError, OSError):
            pass
    return None


def media_relative_path(page: CollectivePage, name: str) -> str:
    """Storage path of an attachment relative to the media folder.

    Layout: ``YYYY/MM/DD/<page-id>/attachments/<folder-id>/<filename>`` so
    attachments can easily be pruned by protocol date when space runs low.
    """
    day = protocol_date(page)
    prefix = day.strftime("%Y/%m/%d") if day else "undated"
    return f"{prefix}/{page.page_id}/attachments/{name}"


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
    """Copy all referenced attachments of a page to the local media folder.

    Already known attachments are kept as-is (attachment file names in
    Collectives are stable), so media referenced only by older versions
    stays available — and files manually pruned from disk to free space are
    not downloaded again.
    """
    paths = extract_attachment_paths(page.content or "")
    if not paths:
        return

    if not settings.nextcloud.base_url:
        logger.debug("Nextcloud not configured, skipping media sync")
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
            file_path=media_relative_path(page, name),
        )
        try:
            media.write_file(data)
        except OSError as e:
            logger.error(
                "Failed to write attachment %s for page %s to %s: %s",
                name,
                page.page_id,
                media.absolute_path,
                e,
            )
            continue
        media.store()
        existing.add(name)
        logger.info(
            "Stored attachment %s (%d bytes) for page %s at %s",
            name,
            len(data),
            page.page_id,
            media.file_path,
        )
