"""Unit tests for protocol versioning, media extraction and the renderer."""

import time
from datetime import date, timedelta
from unittest.mock import patch

from app.models.collective_page import CollectivePage, PageSubtype
from app.models.protocol import Protocol
from app.models.protocol_version import ProtocolVersion
from app.services.collectives_loader import delete_orphaned_pages
from app.services.protocol_media import extract_attachment_paths
from app.services.protocol_render import (
    render_diff_html,
    render_protocol_html,
    rewrite_media_urls,
)


def _make_page(content: str, **kwargs) -> CollectivePage:
    page = CollectivePage(page_id=kwargs.pop("page_id", 42))
    page.title = kwargs.pop("title", "2026-07-01 AG Garten")
    page.content = content
    page.last_user_id = kwargs.pop("last_user_id", "anna")
    page.timestamp = kwargs.pop("timestamp", 1751900000)
    return page


def _make_version(version: int, content: str) -> ProtocolVersion:
    v = ProtocolVersion(page_id=42, version=version)
    v.content = content
    return v


class TestVersionRecording:
    def test_first_version_has_no_diff_and_records_editor(self):
        page = _make_page("# Agenda\n")
        stored = []
        with (
            patch.object(ProtocolVersion, "fetch", return_value=[]),
            patch.object(ProtocolVersion, "store", lambda self: stored.append(self)),
        ):
            created = ProtocolVersion.record(page)

        assert created is not None
        assert created.version == 1
        assert created.diff == ""
        assert created.editor == "anna"
        assert created.content == "# Agenda\n"
        assert created.page_timestamp == 1751900000
        assert stored == [created]

    def test_unchanged_content_records_nothing(self):
        page = _make_page("# Agenda\n")
        latest = _make_version(3, "# Agenda\n")
        with (
            patch.object(ProtocolVersion, "fetch", return_value=[latest]),
            patch.object(ProtocolVersion, "store") as store,
        ):
            assert ProtocolVersion.record(page) is None
        store.assert_not_called()

    def test_changed_content_creates_next_version_with_diff(self):
        page = _make_page("# Agenda\n\n## Neu\n", last_user_id="bob")
        latest = _make_version(1, "# Agenda\n")
        with (
            patch.object(ProtocolVersion, "fetch", return_value=[latest]),
            patch.object(ProtocolVersion, "store", lambda self: None),
        ):
            created = ProtocolVersion.record(page)

        assert created.version == 2
        assert created.editor == "bob"
        assert "+## Neu" in created.diff
        assert "--- v1" in created.diff
        assert "+++ v2" in created.diff

    def test_compute_diff_marks_removed_lines(self):
        diff = ProtocolVersion.compute_diff("a\nb\n", "a\nc\n", "v1", "v2")
        assert "-b" in diff
        assert "+c" in diff


class TestOrphanedProtocolProtection:
    """delete_orphaned_pages must never delete protocols older than 7 days."""

    def _run_cleanup(self, page, protocol):
        removed = []
        with (
            patch.object(CollectivePage, "fetch", return_value=[page]),
            patch.object(
                CollectivePage, "remove", lambda self: removed.append(self.page_id)
            ),
            patch.object(Protocol, "fetch_one", return_value=protocol),
            # the config-based fallback classification is not under test here
            patch.object(Protocol, "is_protocol_page", return_value=False),
        ):
            delete_orphaned_pages(fetched_page_ids=set())
        return removed

    def test_old_protocol_is_kept(self):
        page = _make_page("# Agenda\n")
        page.subtype = PageSubtype.PROTOCOL
        old_date = (date.today() - timedelta(days=30)).isoformat()
        protocol = Protocol(page_id=42, date=old_date)
        assert self._run_cleanup(page, protocol) == []

    def test_recent_protocol_is_deleted(self):
        page = _make_page("# Agenda\n")
        page.subtype = PageSubtype.PROTOCOL
        recent_date = (date.today() - timedelta(days=2)).isoformat()
        protocol = Protocol(page_id=42, date=recent_date)
        assert self._run_cleanup(page, protocol) == [42]

    def test_protocol_with_unknown_age_is_kept(self):
        page = _make_page("# Agenda\n", timestamp=None)
        page.subtype = PageSubtype.PROTOCOL
        protocol = Protocol(page_id=42, date="kein datum")
        assert self._run_cleanup(page, protocol) == []

    def test_old_page_timestamp_protects_when_date_is_missing(self):
        page = _make_page("# Agenda\n", timestamp=int(time.time()) - 30 * 86400)
        page.subtype = PageSubtype.PROTOCOL
        assert self._run_cleanup(page, protocol=None) == []

    def test_non_protocol_page_is_deleted(self):
        page = _make_page("# Some page\n")
        page.subtype = None
        assert self._run_cleanup(page, protocol=None) == [42]

    def test_page_still_in_nextcloud_is_untouched(self):
        page = _make_page("# Agenda\n")
        page.subtype = PageSubtype.PROTOCOL
        removed = []
        with (
            patch.object(CollectivePage, "fetch", return_value=[page]),
            patch.object(
                CollectivePage, "remove", lambda self: removed.append(self.page_id)
            ),
        ):
            delete_orphaned_pages(fetched_page_ids={42})
        assert removed == []


class TestAttachmentExtraction:
    def test_extracts_attachment_paths(self):
        content = (
            "![Foto](.attachments.123/plan.png)\n"
            "![Zwei](./.attachments.123/zwei%20drei.jpg)\n"
            "[Datei](.attachments.123/notes.pdf)\n"
            "![Extern](https://example.com/x.png)\n"
            "[Seite](anderes/seite.md)\n"
        )
        paths = extract_attachment_paths(content)
        assert paths == [
            ".attachments.123/plan.png",
            ".attachments.123/zwei%20drei.jpg",
            ".attachments.123/notes.pdf",
        ]

    def test_duplicates_are_returned_once(self):
        content = "![a](.attachments.1/x.png)\n![b](.attachments.1/x.png)\n"
        assert extract_attachment_paths(content) == [".attachments.1/x.png"]

    def test_no_attachments(self):
        assert extract_attachment_paths("# Nothing here\n") == []
        assert extract_attachment_paths("") == []


class TestRenderer:
    def test_media_urls_are_rewritten_to_local_route(self):
        md = "![Foto](.attachments.123/G%C3%A4rten%20plan.png)"
        rewritten = rewrite_media_urls(md, 42)
        assert rewritten == "![Foto](/protocols/42/media/G%C3%A4rten%20plan.png)"

    def test_render_full_protocol(self):
        content = (
            "Moderation: [Anna](mention://user/anna)\n\n---\n\n# Agenda\n\n"
            "![Foto](.attachments.123/plan.png)\n\n"
            "::: success\n**Beschluss:** Neue Gießkanne kaufen\n:::\n"
        )
        html = render_protocol_html(content, 42, {"anna": "Anna A."})
        assert '<span class="mention">@Anna</span>' in html
        assert 'src="/protocols/42/media/plan.png"' in html
        assert 'class="callout callout-success"' in html
        # markdown inside the callout is still processed
        assert "<strong>Beschluss:</strong>" in html

    def test_bare_mentions_use_display_names(self):
        html = render_protocol_html(
            "Anwesend: mention://user/anna", 1, {"anna": "Anna A."}
        )
        assert '<span class="mention">@Anna A.</span>' in html

    def test_empty_content_renders_empty(self):
        assert render_protocol_html("", 1) == ""
        assert render_protocol_html(None, 1) == ""

    def test_diff_rendering_classifies_lines(self):
        diff = "--- v1\n+++ v2\n@@ -1 +1 @@\n-alt\n+neu\n context"
        html = render_diff_html(diff)
        assert '<span class="diff-del">-alt</span>' in html
        assert '<span class="diff-add">+neu</span>' in html
        assert '<span class="diff-hunk">@@ -1 +1 @@</span>' in html

    def test_diff_rendering_escapes_html(self):
        html = render_diff_html("+<script>alert(1)</script>")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
