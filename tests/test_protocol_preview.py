"""Unit tests for Protocol.compute_preview() and its helpers."""

from unittest.mock import Mock, patch


from app.models.decision import Decision
from app.models.protocol import Protocol


def _make_protocol(**kwargs) -> Protocol:
    """Build a Protocol with sensible defaults for preview tests."""
    defaults = dict(
        page_id=12345,
        date="2024-11-07",
        group_page_id=None,
        moderated_by=[],
        protocol_by=[],
        participants=[],
        location_type="",
        preview="",
    )
    defaults.update(kwargs)
    return Protocol(**defaults)


def _mock_decision(title: str) -> Mock:
    d = Mock()
    d.title = title
    return d


def _mock_page(content: str) -> Mock:
    page = Mock()
    page.content = content
    return page


class TestBodyHeadings:
    """Tests for Protocol._body_headings()."""

    def test_extracts_all_headings_after_horizontal_rule(self):
        p = _make_protocol()
        content = "Moderation: @alice\n---\n\n# Tagesordnung\n\n## TOP 1: Budget\n\nText.\n\n## TOP 2: Planung"
        with patch.object(Protocol, "page", property(lambda self: _mock_page(content))):
            headings = p._body_headings()
        assert "Tagesordnung" in headings
        assert "TOP 1: Budget" in headings
        assert "TOP 2: Planung" in headings

    def test_extracts_headings_when_no_horizontal_rule(self):
        p = _make_protocol()
        content = "Moderation: @alice\n# Tagesordnung\n\n## TOP 1: Budget"
        with patch.object(Protocol, "page", property(lambda self: _mock_page(content))):
            headings = p._body_headings()
        assert "Tagesordnung" in headings
        assert "TOP 1: Budget" in headings

    def test_strips_markdown_formatting_from_headings(self):
        p = _make_protocol()
        content = "---\n\n## **Bold** _italic_ heading"
        with patch.object(Protocol, "page", property(lambda self: _mock_page(content))):
            headings = p._body_headings()
        assert headings == ["Bold italic heading"]

    def test_skips_empty_headings(self):
        p = _make_protocol()
        content = "---\n\n##\n\n## Real heading\n\n###"
        with patch.object(Protocol, "page", property(lambda self: _mock_page(content))):
            headings = p._body_headings()
        assert headings == ["Real heading"]

    def test_returns_empty_when_no_page(self):
        p = _make_protocol()
        with patch.object(Protocol, "page", property(lambda self: None)):
            assert p._body_headings() == []

    def test_returns_empty_when_no_headings_in_body(self):
        p = _make_protocol()
        content = "Moderation: @alice\n---\n\nJust some text, no headings here."
        with patch.object(Protocol, "page", property(lambda self: _mock_page(content))):
            assert p._body_headings() == []

    def test_returns_empty_when_no_header_marker(self):
        p = _make_protocol()
        content = "Just text without any header delimiter or heading."
        with patch.object(Protocol, "page", property(lambda self: _mock_page(content))):
            assert p._body_headings() == []

    def test_returns_empty_for_empty_content(self):
        p = _make_protocol()
        with patch.object(Protocol, "page", property(lambda self: _mock_page(""))):
            assert p._body_headings() == []


class TestComputePreview:
    """Tests for the assembled preview string."""

    def test_includes_all_headings(self):
        p = _make_protocol()
        content = "---\n\n# Tagesordnung\n\n## TOP 1: Budget\n\n## TOP 2: Planung\n\n## TOP 3: Feedback"
        with patch.object(Decision, "fetch", return_value=[]):
            with patch.object(
                Protocol, "page", property(lambda self: _mock_page(content))
            ):
                preview = p.compute_preview()
        assert "TOP 1: Budget" in preview
        assert "TOP 2: Planung" in preview
        assert "TOP 3: Feedback" in preview

    def test_includes_all_decisions(self):
        p = _make_protocol()
        decisions = [_mock_decision(f"Decision {i}") for i in range(5)]
        with patch.object(Decision, "fetch", return_value=decisions):
            with patch.object(Protocol, "page", property(lambda self: None)):
                preview = p.compute_preview()
        for i in range(5):
            assert f"Decision {i}" in preview

    def test_includes_headings_and_decisions(self):
        p = _make_protocol()
        content = "---\n\n# Tagesordnung\n\n## TOP 1: Budget"
        decisions = [_mock_decision("Budget genehmigt"), _mock_decision("Neuer Stuhl")]
        with patch.object(Decision, "fetch", return_value=decisions):
            with patch.object(
                Protocol, "page", property(lambda self: _mock_page(content))
            ):
                preview = p.compute_preview()
        assert "TOP 1: Budget" in preview
        assert "Budget genehmigt" in preview
        assert "Neuer Stuhl" in preview

    def test_does_not_include_date_or_group_lead(self):
        p = _make_protocol(
            date="2024-11-07",
            group_page_id=None,
            moderated_by=["alice"],
            participants=["bob", "carol"],
            location_type="online",
        )
        decisions = [_mock_decision("Budget genehmigt")]
        with patch.object(Decision, "fetch", return_value=decisions):
            with patch.object(Protocol, "page", property(lambda self: None)):
                preview = p.compute_preview()
        # The old lead (date · group · attendees — ) must NOT be in the preview
        assert "2024-11-07" not in preview
        assert "—" not in preview

    def test_skips_decisions_with_empty_title(self):
        p = _make_protocol()
        decisions = [_mock_decision(""), _mock_decision("Real decision")]
        with patch.object(Decision, "fetch", return_value=decisions):
            with patch.object(Protocol, "page", property(lambda self: None)):
                preview = p.compute_preview()
        assert "Real decision" in preview

    def test_empty_when_no_headings_and_no_decisions(self):
        p = _make_protocol()
        with patch.object(Decision, "fetch", return_value=[]):
            with patch.object(Protocol, "page", property(lambda self: None)):
                preview = p.compute_preview()
        assert preview == ""

    def test_empty_when_headings_empty_and_no_decisions(self):
        p = _make_protocol()
        content = "Moderation: @alice\n---\n\nJust text, no headings."
        with patch.object(Decision, "fetch", return_value=[]):
            with patch.object(
                Protocol, "page", property(lambda self: _mock_page(content))
            ):
                preview = p.compute_preview()
        assert preview == ""

    def test_just_decisions_when_no_headings(self):
        p = _make_protocol()
        decisions = [_mock_decision("Budget genehmigt")]
        with patch.object(Decision, "fetch", return_value=decisions):
            with patch.object(Protocol, "page", property(lambda self: None)):
                preview = p.compute_preview()
        assert "Budget genehmigt" in preview
        # Should not contain the agenda section header since no headings
        assert "Agenda" not in preview

    def test_just_headings_when_no_decisions(self):
        p = _make_protocol()
        content = "---\n\n## TOP 1: Budget"
        with patch.object(Decision, "fetch", return_value=[]):
            with patch.object(
                Protocol, "page", property(lambda self: _mock_page(content))
            ):
                preview = p.compute_preview()
        assert "TOP 1: Budget" in preview
        # Should not contain the decisions section header since no decisions
        assert "Decisions" not in preview

    def test_format_uses_bulleted_lists(self):
        p = _make_protocol()
        content = "---\n\n## TOP 1: Budget\n\n## TOP 2: Planung"
        decisions = [_mock_decision("Beschluss A"), _mock_decision("Beschluss B")]
        with patch.object(Decision, "fetch", return_value=decisions):
            with patch.object(
                Protocol, "page", property(lambda self: _mock_page(content))
            ):
                preview = p.compute_preview()
        # Each item should be on its own line with a bullet
        assert "- TOP 1: Budget" in preview
        assert "- TOP 2: Planung" in preview
        assert "- Beschluss A" in preview
        assert "- Beschluss B" in preview

    def test_no_truncation_of_long_previews(self):
        """All headings and decisions are included regardless of length."""
        p = _make_protocol()
        content = "---\n\n" + "\n\n".join(f"## TOP {i}: Item {i}" for i in range(20))
        decisions = [
            _mock_decision(f"Very long decision title number {i}") for i in range(10)
        ]
        with patch.object(Decision, "fetch", return_value=decisions):
            with patch.object(
                Protocol, "page", property(lambda self: _mock_page(content))
            ):
                preview = p.compute_preview()
        # Everything must be present — no truncation
        for i in range(20):
            assert f"TOP {i}: Item {i}" in preview
        for i in range(10):
            assert f"Very long decision title number {i}" in preview
        assert not preview.endswith("…")
