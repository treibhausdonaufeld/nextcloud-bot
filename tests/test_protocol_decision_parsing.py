"""Unit tests for Protocol decision parsing from markdown content."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from lib.nextcloud.config import OrganisationConfig
from lib.nextcloud.models.base import CouchDBModel
from lib.nextcloud.models.decision import Decision
from lib.nextcloud.models.protocol import Protocol


@pytest.fixture
def mock_bot_config():
    """Provide a mock bot_config with default organisation settings."""
    config = MagicMock()
    config.organisation = OrganisationConfig()
    return config


@pytest.fixture
def mock_protocol(mock_bot_config):
    """Create a mock Protocol instance for testing."""
    with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
        # Mock Group.get to avoid database lookups
        with patch("lib.nextcloud.models.protocol.Group") as MockGroup:
            mock_group_instance = Mock()
            mock_group_instance.name = "Test Group"
            mock_group_instance.id = "group_123"
            MockGroup.get.return_value = mock_group_instance

            protocol = Protocol(
                page_id=12345,
                date="2024-11-07 Meeting",
                group_id="group_123",
            )
            yield protocol


@pytest.fixture
def mock_page():
    """Create a mock page object."""
    page = Mock()
    page.content = ""
    page.title = "2024-11-07 Test Protocol"
    page.page_id = 12345
    return page


@pytest.fixture
def mock_group():
    """Create a mock group object."""
    group = Mock()
    group.name = "Test Group"
    group.id = "group_123"
    return group


@pytest.fixture
def mock_decision_instance():
    """Create a mock Decision instance with default attributes."""
    mock_decision = Mock()
    mock_decision.text = ""
    mock_decision.valid_until = None
    mock_decision.objections = None
    return mock_decision


class TestProtocolDecisionExtraction:
    """Test suite for Protocol.extract_decisions() method."""

    def test_extract_single_decision(self, mock_protocol, mock_page, mock_bot_config):
        """Test extracting a single decision from protocol content."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            mock_page.content = """
# Test Protocol

::: success
**Entscheidung:** We approve the budget
This is the decision text.
:::
"""

            with patch.object(Protocol, "page", property(lambda self: mock_page)):
                with patch.object(Decision, "get_all", return_value=[]):
                    with patch.object(Decision, "save"):
                        mock_protocol.extract_decisions()
                        # Decision extraction completed successfully

    def test_extract_multiple_decisions(
        self, mock_protocol, mock_page, mock_bot_config
    ):
        """Test extracting multiple decisions from protocol content."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            mock_page.content = """
# Test Protocol

::: success
**Decision:** First decision
Text for first decision.
:::

Some other content

::: success
**Beschluss:** Second decision
Text for second decision.
:::
"""

            with patch.object(Protocol, "page", property(lambda self: mock_page)):
                with patch.object(Decision, "get_all", return_value=[]):
                    with patch.object(Decision, "save"):
                        mock_protocol.extract_decisions()
                        # Multiple decisions extracted successfully

    def test_skip_extraction_for_future_protocols(
        self, mock_protocol, mock_page, mock_bot_config
    ):
        """Test that decisions are not extracted from future protocols."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            # Set date to future
            future_date = datetime.now().date()
            future_date = future_date.replace(year=future_date.year + 1)
            mock_protocol.date = future_date.strftime("%Y-%m-%d")

            mock_page.content = """
::: success
**Decision:** Future decision
:::
"""

            with patch.object(Protocol, "page", property(lambda self: mock_page)):
                decision_saved = []

                def track_save(self):
                    decision_saved.append(True)

                with patch.object(Decision, "save", track_save):
                    mock_protocol.extract_decisions()

                    # Verify no decisions were saved
                    assert len(decision_saved) == 0

    def test_delete_existing_decisions_before_extraction(
        self, mock_protocol, mock_page, mock_bot_config
    ):
        """Test that existing decisions are deleted before extracting new ones."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            mock_page.content = """
::: success
**Decision:** New decision
:::
"""

            # Mock existing decisions
            mock_decision1 = Mock()
            mock_decision2 = Mock()

            with patch.object(Protocol, "page", property(lambda self: mock_page)):
                with patch.object(
                    Decision, "get_all", return_value=[mock_decision1, mock_decision2]
                ):
                    with patch.object(Decision, "save"):
                        mock_protocol.extract_decisions()

                        # Verify existing decisions were deleted
                        mock_decision1.delete.assert_called_once()
                        mock_decision2.delete.assert_called_once()

    def test_no_content_returns_early(self, mock_protocol, mock_page, mock_bot_config):
        """Test that extract_decisions returns early if page has no content."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            mock_page.content = None

            with patch.object(Protocol, "page", property(lambda self: mock_page)):
                with patch.object(Decision, "get_all") as mock_get_all:
                    mock_protocol.extract_decisions()

                    # Verify get_all was not called (early return)
                    mock_get_all.assert_not_called()


class TestProtocolSaveDecision:
    """Test suite for Protocol.save_decision() method."""

    @pytest.fixture
    def mock_decision_class(self, mock_decision_instance):
        """Fixture to patch Decision class with a mock instance."""
        with patch("lib.nextcloud.models.protocol.Decision") as MockDecision:
            MockDecision.return_value = mock_decision_instance
            yield MockDecision

    def test_save_basic_decision(
        self,
        mock_protocol,
        mock_bot_config,
        mock_decision_class,
        mock_decision_instance,
    ):
        """Test saving a basic decision with title and text."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            block = """
**Entscheidung:** Approve the budget
We will approve the budget for next year.
"""
            mock_protocol.save_decision(block)

            # Verify Decision was created and saved
            mock_decision_class.assert_called_once()
            mock_decision_instance.save.assert_called_once()

    @pytest.mark.parametrize(
        "input_block,expected_title",
        [
            ("**Entscheidung:** Buy new equipment", "Buy new equipment"),
            ("**Decision: Buy new equipment**", "Buy new equipment"),
            ("**Beschluss - Buy new equipment**", "Buy new equipment"),
            ("**ENTSCHEIDUNG: Buy new equipment**", "Buy new equipment"),
        ],
    )
    def test_clean_title_from_keywords(
        self,
        mock_protocol,
        mock_bot_config,
        mock_decision_class,
        input_block,
        expected_title,
    ):
        """Test that decision title keywords are removed from title."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            mock_protocol.save_decision(input_block)

            # Check the title passed to Decision constructor
            call_kwargs = mock_decision_class.call_args[1]
            assert call_kwargs["title"] == expected_title

    def test_extract_valid_until(
        self,
        mock_protocol,
        mock_bot_config,
        mock_decision_class,
        mock_decision_instance,
    ):
        """Test extracting 'valid until' information from decision."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            block = """
**Entscheidung:** Temporary decision
This is a temporary decision.
Gültig bis: 2025-12-31
"""
            mock_protocol.save_decision(block)

            # Verify valid_until was set
            assert mock_decision_instance.valid_until == "2025-12-31"

    def test_extract_objections(
        self,
        mock_protocol,
        mock_bot_config,
        mock_decision_class,
        mock_decision_instance,
    ):
        """Test extracting objections from decision."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            block = """
**Decision:** Decision with objections
This has objections.
Einwände: John disagrees with this decision
"""
            mock_protocol.save_decision(block)

            # Verify objections were set
            assert (
                mock_decision_instance.objections == "John disagrees with this decision"
            )

    def test_remove_metadata_lines_from_text(
        self,
        mock_protocol,
        mock_bot_config,
        mock_decision_class,
        mock_decision_instance,
    ):
        """Test that metadata lines are removed from decision text."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            block = """
**Decision:** Test decision
This is the decision text.
Gültig bis: 2025-12-31
More decision text here.
Einwände: Some objections
"""
            mock_protocol.save_decision(block)

            # Verify text has metadata removed but decision text intact
            decision_text = mock_decision_instance.text
            assert "Gültig bis:" not in decision_text
            assert "Einwände:" not in decision_text
            assert "This is the decision text." in decision_text
            assert "More decision text here." in decision_text

    def test_use_text_as_title_if_no_title(
        self, mock_protocol, mock_bot_config, mock_decision_class
    ):
        """Test that first line of text is used as title if no title line found."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            block = """
This is decision text without a title line.
More text here.
"""
            mock_protocol.save_decision(block)

            # Verify first line was used as title
            call_kwargs = mock_decision_class.call_args[1]
            assert call_kwargs["title"] == "This is decision text without a title line."

    def test_decision_with_formatting(
        self, mock_protocol, mock_bot_config, mock_decision_class
    ):
        """Test decision with markdown formatting in title."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            block = "**Decision:** _Approve_ the **budget**"
            mock_protocol.save_decision(block)

            # Title should have ** removed but preserve _
            call_kwargs = mock_decision_class.call_args[1]
            assert call_kwargs["title"] == "_Approve_ the budget"

    def test_empty_block_returns_early(
        self, mock_protocol, mock_bot_config, mock_decision_class
    ):
        """Test that empty or whitespace-only blocks return without creating decision."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            # Empty string
            mock_protocol.save_decision("")
            mock_decision_class.assert_not_called()

            # Whitespace only
            mock_protocol.save_decision("   \n  \n  ")
            mock_decision_class.assert_not_called()

    def test_decision_includes_group_info(
        self, mock_protocol, mock_bot_config, mock_decision_class
    ):
        """Test that saved decision includes group_id and group_name."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            block = "**Decision:** Test decision"
            mock_protocol.save_decision(block)

            # Verify group information was passed
            call_kwargs = mock_decision_class.call_args[1]
            assert call_kwargs["group_id"] == mock_protocol.group_id
            # Verify group information was passed
            call_kwargs = mock_decision_class.call_args[1]
            assert call_kwargs["group_id"] == mock_protocol.group_id
            assert call_kwargs["group_name"] == "Test Group"


class TestProtocolValidTitle:
    """Test suite for Protocol.valid_title() class method."""

    def test_valid_protocol_titles(self):
        """Test that valid protocol titles are recognized."""
        valid_titles = [
            "2024-11-07 Team Meeting",
            "2025-01-01 New Year Protocol",
            "2023-12-31 Year End Meeting",
            "2024-06-15 Budget Discussion",
        ]

        for title in valid_titles:
            assert Protocol.valid_date(title), f"'{title}' should be valid"

    def test_invalid_protocol_titles(self):
        """Test that invalid protocol titles are rejected."""
        invalid_titles = [
            "Meeting Notes",  # No date
            "2024-11-07",  # No title
            "2024/11/07 Meeting",  # Wrong date format
            "11-07-2024 Meeting",  # Wrong date order
        ]

        for title in invalid_titles:
            assert not Protocol.valid_date(title), f"'{title}' should be invalid"


class TestProtocolDelete:
    """Test suite for Protocol.delete() method."""

    def test_delete_protocol_and_decisions(self, mock_protocol, mock_bot_config):
        """Test that deleting protocol also deletes associated decisions."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            # Mock existing decisions
            mock_decision1 = Mock()
            mock_decision2 = Mock()

            with patch.object(
                Decision, "get_all", return_value=[mock_decision1, mock_decision2]
            ):
                # Mock the base class delete method
                with patch.object(CouchDBModel, "delete"):
                    mock_protocol.delete()

                    # Verify decisions were deleted
                    mock_decision1.delete.assert_called_once()
                    mock_decision2.delete.assert_called_once()

    def test_delete_with_no_decisions(self, mock_protocol, mock_bot_config):
        """Test that delete works when protocol has no associated decisions."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            with patch.object(Decision, "get_all", return_value=[]):
                # Mock the base class delete method
                with patch.object(CouchDBModel, "delete"):
                    # Should not raise an error
                    mock_protocol.delete()


class TestProtocolDecisionKeywordVariations:
    """Test suite for various keyword variations in different languages."""

    @pytest.fixture
    def mock_decision_class(self, mock_decision_instance):
        """Fixture to patch Decision class with a mock instance."""
        with patch("lib.nextcloud.models.protocol.Decision") as MockDecision:
            MockDecision.return_value = mock_decision_instance
            yield MockDecision

    @pytest.mark.parametrize(
        "keyword,expected_title",
        [
            ("Entscheidung:", "Buy new equipment"),
            ("Decision:", "Buy new equipment"),
            ("Beschluss:", "Buy new equipment"),
            ("ENTSCHEIDUNG:", "Buy new equipment"),
            ("entscheidung:", "Buy new equipment"),
        ],
    )
    def test_decision_title_keywords(
        self,
        mock_protocol,
        mock_bot_config,
        mock_decision_class,
        keyword,
        expected_title,
    ):
        """Test that various decision keywords are recognized and removed."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            block = f"**{keyword}** Buy new equipment"
            mock_protocol.save_decision(block)

            call_kwargs = mock_decision_class.call_args[1]
            assert call_kwargs["title"] == expected_title

    @pytest.mark.parametrize(
        "keyword,expected_date",
        [
            ("Gültig bis:", "2025-12-31"),
            ("Valid until:", "2025-06-30"),
            ("Befristet auf:", "2024-12-31"),
        ],
    )
    def test_valid_until_keywords(
        self,
        mock_protocol,
        mock_bot_config,
        mock_decision_class,
        mock_decision_instance,
        keyword,
        expected_date,
    ):
        """Test that various 'valid until' keywords are recognized."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            block = f"""
**Decision:** Test decision
{keyword} {expected_date}
"""
            mock_protocol.save_decision(block)
            assert mock_decision_instance.valid_until == expected_date

    @pytest.mark.parametrize(
        "keyword,expected_objection",
        [
            ("Einwände:", None),
            ("Objections:", "Two members objected"),
            ("Einwand:", "Single objection"),
        ],
    )
    def test_objection_keywords(
        self,
        mock_protocol,
        mock_bot_config,
        mock_decision_class,
        mock_decision_instance,
        keyword,
        expected_objection,
    ):
        """Test that various objection keywords are recognized."""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            objection_text = expected_objection if expected_objection else ""
        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            objection_text = expected_objection if expected_objection else ""
            block = f"""
**Decision:** Test decision
{keyword} {objection_text}
"""
            mock_protocol.save_decision(block)

            if expected_objection:
                assert mock_decision_instance.objections == expected_objection
            else:
                # Empty objection should result in None or empty string
                assert mock_decision_instance.objections in [None, ""]


class TestProtocolNotificationDateConstraints:
    """Test suite for Protocol notification date constraints."""

    @pytest.fixture
    def protocol_page_content(self):
        """Standard protocol page content for testing."""
        return """
# Test Protocol

## Moderation: mention://user/alice
## Protocol: mention://user/bob
## Participants: mention://user/charlie
"""

    def _test_notification_with_date_offset(
        self,
        days_offset: int,
        should_notify: bool,
        mock_protocol,
        mock_page,
        mock_bot_config,
        mock_group,
        protocol_page_content,
    ):
        """Helper method to test notification behavior for different date offsets.

        Args:
            days_offset: Number of days before today (positive = past, negative = future)
            should_notify: Whether notify_updated should be called
        """
        test_date = datetime.now().date() - timedelta(days=days_offset)
        date_str = test_date.strftime("%Y-%m-%d")

        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            mock_page.title = f"{date_str} Test Group"
            mock_page.content = protocol_page_content

            with patch.object(Protocol, "page", property(lambda self: mock_page)):
                with patch.object(Decision, "get_all", return_value=[]):
                    with patch("lib.nextcloud.models.protocol.Group") as MockGroup:
                        MockGroup.get_for_page.return_value = mock_group
                        MockGroup.get.return_value = mock_group

                        with patch.object(Protocol, "notify_updated") as mock_notify:
                            with patch.object(CouchDBModel, "save"):
                                mock_protocol.summary_posted = False
                                mock_protocol.update_from_page()

                                if should_notify:
                                    mock_notify.assert_called_once()
                                else:
                                    mock_notify.assert_not_called()

    @pytest.mark.parametrize(
        "days_offset,should_notify,description",
        [
            (0, True, "today's protocol"),
            (15, False, "15-day-old protocol (too old)"),
            (1, True, "yesterday's protocol"),
            (7, True, "7-day-old protocol"),
            (14, True, "14-day-old protocol (edge case)"),
        ],
    )
    def test_notification_date_constraints(
        self,
        days_offset,
        should_notify,
        description,
        mock_protocol,
        mock_page,
        mock_bot_config,
        mock_group,
        protocol_page_content,
    ):
        """Test that notification respects date constraints for various protocol ages."""
        self._test_notification_with_date_offset(
            days_offset,
            should_notify,
            mock_protocol,
            mock_page,
            mock_bot_config,
            mock_group,
            protocol_page_content,
        )

    def test_protocol_cooldown_respected(
        self,
        mock_protocol,
        mock_page,
        mock_bot_config,
        mock_group,
        protocol_page_content,
    ):
        """Ensure protocol is not parsed while within cooldown and is parsed after cooldown expires."""
        # Use datetime.timestamp() to avoid importing time in this test file
        now_ts = datetime.now().timestamp()

        with patch("lib.nextcloud.models.protocol.bot_config", mock_bot_config):
            # Ensure a known cooldown value
            mock_bot_config.organisation.protocol_cooldown_minutes = 60

            # Prepare page with recent timestamp (within cooldown)
            mock_page.title = f"{datetime.now().date().strftime('%Y-%m-%d')} Test Group"
            mock_page.content = protocol_page_content
            mock_page.timestamp = now_ts
            mock_page.ocs = Mock()
            mock_page.ocs.timestamp = now_ts

            with patch.object(Protocol, "page", property(lambda self: mock_page)):
                with patch.object(Decision, "get_all", return_value=[]):
                    with patch("lib.nextcloud.models.protocol.Group") as MockGroup:
                        MockGroup.get_for_page.return_value = mock_group
                        MockGroup.get.return_value = mock_group

                        with patch.object(Protocol, "notify_updated") as mock_notify:
                            with patch.object(CouchDBModel, "save"):
                                # First update: timestamp is recent -> should be skipped due to cooldown
                                mock_protocol.update_from_page()
                                mock_notify.assert_not_called()

                                # Now simulate timestamp older than cooldown -> should trigger parsing/notification
                                old_ts = (
                                    now_ts
                                    - (
                                        mock_bot_config.organisation.protocol_cooldown_minutes
                                        * 60
                                    )
                                    - 1
                                )
                                mock_page.timestamp = old_ts
                                mock_page.ocs.timestamp = old_ts

                                mock_protocol.update_from_page()
                                mock_notify.assert_called_once()
