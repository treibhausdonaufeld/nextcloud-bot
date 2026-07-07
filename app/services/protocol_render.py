"""Render protocol markdown to HTML for the in-app protocol viewer.

Handles the Nextcloud-specific markdown constructs found in Collectives
pages: ``::: success`` style callout blocks, ``mention://user/...`` links and
relative attachment links (rewritten to the app's own media route, see
`app.services.protocol_media`).
"""

from __future__ import annotations

import html
import re
from pathlib import PurePosixPath
from urllib.parse import quote

import markdown as markdown_lib
import nh3
from markupsafe import Markup

from app.services.protocol_media import media_name

# "md_in_html" (part of "extra") lets the callout <div> wrappers keep
# markdown-processed content via the markdown="1" attribute.
_MD_EXTENSIONS = ["extra", "nl2br", "sane_lists"]

# Page content comes from the Nextcloud wiki and may contain raw HTML —
# sanitize the rendered output so embedded scripts cannot execute in the
# viewer. The nh3 defaults cover the markdown output; `class` is additionally
# allowed for the callout divs and mention spans, `target`/`download` for
# the attachment links (nh3 adds rel="noopener noreferrer" itself).
_ALLOWED_ATTRIBUTES = {k: set(v) for k, v in nh3.ALLOWED_ATTRIBUTES.items()}
for _tag in ("div", "span", "a", "img", "code", "pre"):
    _ALLOWED_ATTRIBUTES.setdefault(_tag, set()).add("class")
_ALLOWED_ATTRIBUTES["a"] |= {"title", "target", "download"}
_ALLOWED_ATTRIBUTES["img"] |= {"title"}

# Extensions rendered inline as images; everything else becomes a download
# link. SVG is safe here: the media route's CSP blocks scripts, and scripts
# never run inside <img> anyway.
_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".bmp",
    ".avif",
}

# A full markdown link/image whose target points into an attachments folder:
# optional leading "!", the link text, then the attachment path (optionally
# "./"-prefixed, angle-bracketed or followed by a title).
_ATTACHMENT_MD_RE = re.compile(
    r"(!?)\[([^\]]*)\]"
    r"\(\s*<?((?:\./)?\.attachments\.\d+/[^)\s<>]+)>?(?:\s+\"[^\"]*\")?\s*\)"
)

_CALLOUT_RE = re.compile(
    r"^:::[ \t]*(\w+)[ \t]*\r?\n(.*?)^:::[ \t]*$",
    flags=re.DOTALL | re.MULTILINE,
)

_MENTION_LINK_RE = re.compile(r"@?\[([^\]]*)\]\(mention://user/([A-Za-z0-9_.-]+)\)")
_BARE_MENTION_RE = re.compile(r"mention://user/([A-Za-z0-9_.-]+)")


def rewrite_media_urls(content: str, page_id: int) -> str:
    """Turn attachment references into inline images or download links.

    Image attachments (by file extension) are shown inline and wrapped in a
    link that opens the full-size file in a new tab — regardless of whether
    the markdown used image or plain link syntax. All other attachments
    (PDFs, documents, ...) become download links served by the app.

    The served name keeps the attachment folder id ("<folder-id>/<filename>"),
    so same-named files from different attachment folders cannot collide.
    """

    def replace(match: re.Match) -> str:
        text = match.group(2).strip()
        name = media_name(match.group(3))
        url = f"/protocols/{page_id}/media/{quote(name, safe='/')}"
        filename = name.split("/", 1)[-1]

        if PurePosixPath(filename).suffix.lower() in _IMAGE_EXTENSIONS:
            alt = html.escape(text or filename)
            return (
                f'<a href="{url}" target="_blank"><img src="{url}" alt="{alt}" /></a>'
            )

        label = html.escape(text or filename)
        return (
            f'<a class="attachment-download" href="{url}"'
            f' download="{html.escape(filename)}">📎 {label}</a>'
        )

    return _ATTACHMENT_MD_RE.sub(replace, content)


def _replace_callouts(content: str) -> str:
    """Turn ``::: success ... :::`` blocks into styled callout divs."""

    def replace(match: re.Match) -> str:
        kind = match.group(1).lower()
        body = match.group(2).strip("\n")
        return f'<div class="callout callout-{kind}" markdown="1">\n\n{body}\n\n</div>'

    return _CALLOUT_RE.sub(replace, content)


def _replace_mentions(content: str, user_names: dict[str, str] | None = None) -> str:
    """Turn mention:// links into inline mention badges."""
    names = user_names or {}

    def link_replace(match: re.Match) -> str:
        text = match.group(1).lstrip("@").strip() or match.group(2)
        return f'<span class="mention">@{html.escape(text)}</span>'

    def bare_replace(match: re.Match) -> str:
        username = match.group(1)
        text = names.get(username, username)
        text = text.lstrip("@").strip() or username
        return f'<span class="mention">@{html.escape(text)}</span>'

    content = _MENTION_LINK_RE.sub(link_replace, content)
    return _BARE_MENTION_RE.sub(bare_replace, content)


def render_protocol_html(
    content: str | None,
    page_id: int,
    user_names: dict[str, str] | None = None,
) -> Markup:
    """Render protocol markdown to HTML (media, callouts and mentions aware)."""
    if not content:
        return Markup("")

    text = rewrite_media_urls(content, page_id)
    text = _replace_callouts(text)
    text = _replace_mentions(text, user_names)
    # A fresh conversion per call keeps this safe under Ravyn's threadpool.
    rendered = markdown_lib.markdown(text, extensions=_MD_EXTENSIONS)
    return Markup(nh3.clean(rendered, attributes=_ALLOWED_ATTRIBUTES))


def render_diff_html(diff: str | None) -> Markup:
    """Render a stored unified diff as color-coded HTML lines."""
    if not diff:
        return Markup("")

    rendered = []
    for line in diff.splitlines():
        escaped = html.escape(line)
        if line.startswith(("+++", "---")):
            css = "diff-file"
        elif line.startswith("@@"):
            css = "diff-hunk"
        elif line.startswith("+"):
            css = "diff-add"
        elif line.startswith("-"):
            css = "diff-del"
        else:
            css = "diff-ctx"
        rendered.append(f'<span class="{css}">{escaped}</span>')
    return Markup("<pre class='diff'>" + "\n".join(rendered) + "</pre>")
