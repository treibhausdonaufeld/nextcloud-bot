"""Unit tests for Group parsing from collective page content."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from lib.nextcloud.config import OrganisationConfig
from lib.nextcloud.models.collective_page import CollectivePage
from lib.nextcloud.models.group import Group


@pytest.fixture
def mock_bot_config():
    """Provide a mock bot_config with default organisation settings."""
    config = MagicMock()
    config.organisation = OrganisationConfig()
    return config


@pytest.fixture
def mock_page():
    """Create a mock CollectivePage object."""
    page = Mock(spec=CollectivePage)
    page.content = ""
    page.full_path = "AG Test Group"
    page.page_id = 12345
    page.ocs = Mock()
    page.ocs.filePath = "AG Test Group/README.md"
    page.ocs.emoji = "🏢"
    return page


@pytest.fixture
def mock_group(mock_page):
    """Create a mock Group instance for testing."""
    group = Group(
        name="AG Test Group",
        page_id=mock_page.page_id,
        emoji="🏢",
    )
    return group


class TestGroupShortnameParsing:
    """Test suite for Group shortname parsing from page content."""

    def test_parse_single_shortname(self, mock_group, mock_page, mock_bot_config):
        """Test parsing a single shortname from page content."""
        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            mock_page.content = """
# AG Test Group

**Kurznamen:** Test, AG-Test

## Members
mention://user/alice
"""

            with patch.object(
                CollectivePage, "get_from_page_id", return_value=mock_page
            ):
                with patch.object(Group, "save"):
                    mock_group.update_from_page()

                    # Shortnames are lowercased
                    assert "test" in mock_group.short_names
                    assert "ag-test" in mock_group.short_names
                    assert len(mock_group.short_names) == 2

    def test_parse_shortnames_with_different_keywords(
        self, mock_group, mock_page, mock_bot_config
    ):
        """Test that various shortname keywords are recognized."""
        test_cases = [
            ("Schlagwörter:", ["tag1", "tag2"]),  # Lowercased
            ("Kurznamen:", ["short1", "short2"]),  # Lowercased
            ("Shortnames:", ["name1", "name2"]),  # Lowercased
        ]

        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            for keyword, expected_names in test_cases:
                mock_page.content = f"""
# AG Test Group

**{keyword}** {", ".join(expected_names)}

## Members
mention://user/alice
"""
                mock_group.short_names = []  # Reset for each test

                with patch.object(
                    CollectivePage, "get_from_page_id", return_value=mock_page
                ):
                    with patch.object(Group, "save"):
                        mock_group.update_from_page()

                        for name in expected_names:
                            assert name in mock_group.short_names, (
                                f"'{name}' should be in short_names for keyword '{keyword}'"
                            )

    def test_parse_empty_shortnames(self, mock_group, mock_page, mock_bot_config):
        """Test parsing when no shortnames are provided."""
        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            mock_page.content = """
# AG Test Group

## Members
mention://user/alice
"""

            with patch.object(
                CollectivePage, "get_from_page_id", return_value=mock_page
            ):
                with patch.object(Group, "save"):
                    mock_group.update_from_page()

                    assert mock_group.short_names == []

    def test_parse_shortnames_sorted(self, mock_group, mock_page, mock_bot_config):
        """Test that shortnames are sorted alphabetically."""
        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            mock_page.content = """
# AG Test Group

**Kurznamen:** kurznamen, Zebra, Apple, Mango

## Members
mention://user/alice
"""

            with patch.object(
                CollectivePage, "get_from_page_id", return_value=mock_page
            ):
                with patch.object(Group, "save"):
                    mock_group.update_from_page()

                    # Should be sorted alphabetically
                    assert mock_group.short_names == sorted(mock_group.short_names)


class TestGroupMemberParsing:
    """Test suite for Group member/coordination/delegate parsing."""

    def test_parse_coordination_members(self, mock_group, mock_page, mock_bot_config):
        """Test parsing coordination members from page content."""
        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            mock_page.content = """
# AG Test Group

**Koordination:**
mention://user/alice
mention://user/bob

## Members
mention://user/charlie
"""

            with patch.object(
                CollectivePage, "get_from_page_id", return_value=mock_page
            ):
                with patch.object(Group, "save"):
                    mock_group.update_from_page()

                    assert "alice" in mock_group.coordination
                    assert "bob" in mock_group.coordination
                    assert "charlie" not in mock_group.coordination

    def test_parse_delegates(self, mock_group, mock_page, mock_bot_config):
        """Test parsing delegate members from page content."""
        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            mock_page.content = """
# AG Test Group

**Delegierte:**
mention://user/dave
mention://user/eve

## Members
mention://user/frank
"""

            with patch.object(
                CollectivePage, "get_from_page_id", return_value=mock_page
            ):
                with patch.object(Group, "save"):
                    mock_group.update_from_page()

                    assert "dave" in mock_group.delegate
                    assert "eve" in mock_group.delegate
                    assert "frank" not in mock_group.delegate

    def test_parse_regular_members(self, mock_group, mock_page, mock_bot_config):
        """Test parsing regular members from page content."""
        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            mock_page.content = """
# AG Test Group

**Mitglieder:**
mention://user/george
mention://user/helen
"""

            with patch.object(
                CollectivePage, "get_from_page_id", return_value=mock_page
            ):
                with patch.object(Group, "save"):
                    mock_group.update_from_page()

                    assert "george" in mock_group.members
                    assert "helen" in mock_group.members

    def test_members_excludes_coordination_and_delegates(
        self, mock_group, mock_page, mock_bot_config
    ):
        """Test that members list excludes people in coordination and delegate lists."""
        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            mock_page.content = """
# AG Test Group

**Koordination:**
mention://user/alice

**Delegierte:**
mention://user/bob

**Mitglieder:**
mention://user/alice
mention://user/bob
mention://user/charlie
"""

            with patch.object(
                CollectivePage, "get_from_page_id", return_value=mock_page
            ):
                with patch.object(Group, "save"):
                    mock_group.update_from_page()

                    # alice and bob should be removed from members since they're in coordination/delegate
                    assert "alice" not in mock_group.members
                    assert "bob" not in mock_group.members
                    assert "charlie" in mock_group.members

                    # But they should still be in their respective lists
                    assert "alice" in mock_group.coordination
                    assert "bob" in mock_group.delegate

    def test_all_members_property(self, mock_group, mock_page, mock_bot_config):
        """Test that all_members property combines all member types."""
        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            mock_page.content = """
# AG Test Group

**Koordination:**
mention://user/alice

**Delegierte:**
mention://user/bob

**Mitglieder:**
mention://user/charlie
"""

            with patch.object(
                CollectivePage, "get_from_page_id", return_value=mock_page
            ):
                with patch.object(Group, "save"):
                    mock_group.update_from_page()

                    all_members = mock_group.all_members
                    assert "alice" in all_members
                    assert "bob" in all_members
                    assert "charlie" in all_members
                    assert len(all_members) == 3

    def test_members_are_sorted(self, mock_group, mock_page, mock_bot_config):
        """Test that all member lists are sorted alphabetically."""
        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            mock_page.content = """
# AG Test Group

**Koordination:**
mention://user/zebra
mention://user/alice
mention://user/bob

**Mitglieder:**
mention://user/yankee
mention://user/charlie
"""

            with patch.object(
                CollectivePage, "get_from_page_id", return_value=mock_page
            ):
                with patch.object(Group, "save"):
                    mock_group.update_from_page()

                    assert mock_group.coordination == sorted(mock_group.coordination)
                    assert mock_group.members == sorted(mock_group.members)


class TestGroupNameAndMetadata:
    """Test suite for Group name and metadata extraction."""

    def test_parse_group_name_from_path(self, mock_group, mock_page, mock_bot_config):
        """Test extracting group name from file path."""
        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            mock_page.full_path = "AG Innovation/README.md"
            mock_page.ocs.filePath = "AG Innovation/README.md"
            mock_page.content = "# AG Innovation"

            with patch.object(
                CollectivePage, "get_from_page_id", return_value=mock_page
            ):
                with patch.object(Group, "save"):
                    mock_group.update_from_page()

                    assert mock_group.name == "AG Innovation"

    def test_parse_parent_group(self, mock_group, mock_page, mock_bot_config):
        """Test extracting parent group from nested path."""
        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            mock_page.full_path = "AG Parent/UG Child/README.md"
            mock_page.ocs.filePath = "AG Parent/UG Child/README.md"
            mock_page.content = "# UG Child"

            with patch.object(
                CollectivePage, "get_from_page_id", return_value=mock_page
            ):
                with patch.object(Group, "save"):
                    mock_group.update_from_page()

                    assert mock_group.parent_group == "AG Parent"

    def test_parse_emoji(self, mock_group, mock_page, mock_bot_config):
        """Test that emoji is preserved from page OCS data."""
        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            mock_page.ocs.emoji = "🚀"
            mock_page.content = "# AG Test"

            with patch.object(
                CollectivePage, "get_from_page_id", return_value=mock_page
            ):
                with patch.object(Group, "save"):
                    mock_group.update_from_page()

                    assert mock_group.emoji == "🚀"

    def test_valid_group_name_with_prefix(self, mock_bot_config):
        """Test that names with valid prefixes are recognized."""
        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            assert Group.valid_name("AG Test Group") is True
            assert Group.valid_name("UG Working Group") is True
            assert Group.valid_name("PG Project") is True

    def test_invalid_group_name_without_prefix(self, mock_bot_config):
        """Test that names without valid prefixes are rejected."""
        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            assert Group.valid_name("Invalid Group") is False
            assert Group.valid_name("Meeting Notes") is False


class TestGroupMemberKeywordVariations:
    """Test suite for various keyword variations in different languages."""

    @pytest.mark.parametrize(
        "keyword",
        [
            "Koordination",
            "Koordinator",
            "Koordinatorin",
            "Koordinator:in",
            "Sprecher",
            "Sprecherin",
            "Sprecher:in",
        ],
    )
    def test_coordination_keywords(
        self, mock_group, mock_page, mock_bot_config, keyword
    ):
        """Test that various coordination keywords are recognized."""
        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            mock_page.content = f"""
# AG Test Group

**{keyword}:**
mention://user/alice
"""

            with patch.object(
                CollectivePage, "get_from_page_id", return_value=mock_page
            ):
                with patch.object(Group, "save"):
                    mock_group.update_from_page()

                    assert "alice" in mock_group.coordination

    @pytest.mark.parametrize(
        "keyword",
        [
            "Delegierter",
            "Delegierte",
        ],
    )
    def test_delegate_keywords(self, mock_group, mock_page, mock_bot_config, keyword):
        """Test that various delegate keywords are recognized."""
        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            mock_page.content = f"""
# AG Test Group

**{keyword}:**
mention://user/bob
"""

            with patch.object(
                CollectivePage, "get_from_page_id", return_value=mock_page
            ):
                with patch.object(Group, "save"):
                    mock_group.update_from_page()

                    assert "bob" in mock_group.delegate

    @pytest.mark.parametrize(
        "keyword",
        [
            "Mitglied",
            "Mitglieder",
        ],
    )
    def test_member_keywords(self, mock_group, mock_page, mock_bot_config, keyword):
        """Test that various member keywords are recognized."""
        with patch("lib.nextcloud.models.group.bot_config", mock_bot_config):
            mock_page.content = f"""
# AG Test Group

**{keyword}:**
mention://user/charlie
"""

            with patch.object(
                CollectivePage, "get_from_page_id", return_value=mock_page
            ):
                with patch.object(Group, "save"):
                    mock_group.update_from_page()

                    assert "charlie" in mock_group.members
