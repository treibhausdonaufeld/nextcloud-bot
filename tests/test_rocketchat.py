"""Tests for the Rocket.Chat webhook sender's case-sensitive channel handling."""

from unittest.mock import Mock, patch

import requests

from app.services import rocketchat
from app.settings import settings


def _response(status_code: int) -> Mock:
    resp = Mock()
    resp.status_code = status_code
    resp.text = f"status {status_code}"
    return resp


class TestCaseVariants:
    def test_first_variant_is_configured_casing(self):
        variants = rocketchat._case_variants("AG-Haus")
        assert variants[0] == "AG-Haus"

    def test_includes_common_alternate_casings(self):
        variants = rocketchat._case_variants("AG-Haus")
        assert "ag-haus" in variants
        assert "AG-HAUS" in variants
        assert "AG-haus" in variants

    def test_no_duplicate_variants_for_already_lowercase_channel(self):
        variants = rocketchat._case_variants("protokolle")
        assert variants == ["protokolle", "PROTOKOLLE", "Protokolle"]


class TestSendRocketchatMessage:
    def test_sends_with_configured_casing_on_first_success(self):
        with (
            patch.object(settings.rocketchat, "hook_url", "https://chat.example/hook"),
            patch.object(settings.rocketchat, "channel_overwrite", ""),
            patch("app.services.rocketchat.requests.post") as mock_post,
        ):
            mock_post.return_value = _response(200)
            rocketchat.send_rocketchat_message("hi", "AG-Haus")

        assert mock_post.call_count == 1
        assert mock_post.call_args.kwargs["json"]["channel"] == "AG-Haus"
        assert mock_post.call_args.kwargs["timeout"] == 90

    def test_retries_other_casings_after_failure(self):
        with (
            patch.object(settings.rocketchat, "hook_url", "https://chat.example/hook"),
            patch.object(settings.rocketchat, "channel_overwrite", ""),
            patch("app.services.rocketchat.requests.post") as mock_post,
        ):
            mock_post.side_effect = [_response(404), _response(200)]
            rocketchat.send_rocketchat_message("hi", "AG-Haus")

        assert mock_post.call_count == 2
        sent_channels = [c.kwargs["json"]["channel"] for c in mock_post.call_args_list]
        assert sent_channels[0] == "AG-Haus"
        assert sent_channels[1] == "ag-haus"

    def test_logs_error_when_all_casings_fail(self):
        with (
            patch.object(settings.rocketchat, "hook_url", "https://chat.example/hook"),
            patch.object(settings.rocketchat, "channel_overwrite", ""),
            patch("app.services.rocketchat.requests.post") as mock_post,
            patch("app.services.rocketchat.logger.error") as mock_error,
        ):
            mock_post.return_value = _response(404)
            rocketchat.send_rocketchat_message("hi", "AG-Haus")

        assert mock_post.call_count == len(rocketchat._case_variants("AG-Haus"))
        mock_error.assert_called_once()

    def test_direct_message_does_not_try_alternate_casings(self):
        with (
            patch.object(settings.rocketchat, "hook_url", "https://chat.example/hook"),
            patch.object(settings.rocketchat, "channel_overwrite", ""),
            patch("app.services.rocketchat.requests.post") as mock_post,
        ):
            mock_post.return_value = _response(404)
            rocketchat.send_rocketchat_message("hi", "@Bob.Beispiel")

        assert mock_post.call_count == 1
        assert mock_post.call_args.kwargs["json"]["channel"] == "@Bob.Beispiel"

    def test_network_error_on_one_candidate_falls_through_to_next(self):
        with (
            patch.object(settings.rocketchat, "hook_url", "https://chat.example/hook"),
            patch.object(settings.rocketchat, "channel_overwrite", ""),
            patch("app.services.rocketchat.requests.post") as mock_post,
        ):
            mock_post.side_effect = [
                requests.ConnectionError("boom"),
                _response(200),
            ]
            rocketchat.send_rocketchat_message("hi", "AG-Haus")

        assert mock_post.call_count == 2
        sent_channels = [c.kwargs["json"]["channel"] for c in mock_post.call_args_list]
        assert sent_channels[1] == "ag-haus"

    def test_no_webhook_configured_does_not_call_requests(self):
        with (
            patch.object(settings.rocketchat, "hook_url", None),
            patch.object(settings.rocketchat, "channel_overwrite", ""),
            patch("app.services.rocketchat.requests.post") as mock_post,
        ):
            rocketchat.send_rocketchat_message("hi", "AG-Haus")

        mock_post.assert_not_called()
