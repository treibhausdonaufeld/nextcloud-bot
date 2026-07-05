"""Tests for importing decisions from a CouchDB JSON export."""

from unittest.mock import patch

from app.models.decision import Decision
from app.services.logbook_import import import_decisions_from_records


class TestImportDecisionsFromRecords:
    def test_imports_all_fields(self):
        records = [
            {
                "title": "Gießkanne kaufen",
                "text": "Wir kaufen eine neue Gießkanne.",
                "date": "2024-01-10",
                "page_id": 300,
                "group_name": "AG Garten",
                "valid_until": "2030-01-01",
                "objections": "Keine",
                "external_link": "https://cloud.example.org/p1",
            }
        ]

        stored = []
        with patch.object(Decision, "store", lambda self: stored.append(self)):
            results = list(import_decisions_from_records(records))

        assert results == [""]
        assert len(stored) == 1
        decision = stored[0]
        assert decision.title == "Gießkanne kaufen"
        assert decision.page_id == 300
        assert decision.group_name == "AG Garten"
        assert decision.valid_until == "2030-01-01"
        assert decision.objections == "Keine"
        assert decision.external_link == "https://cloud.example.org/p1"
        # natural key matches what protocol re-parsing generates, so a later
        # sync --update-all replaces this row instead of duplicating it
        assert decision.build_natural_key() == "300:Gießkanne kaufen"

    def test_manual_decision_without_page_id(self):
        records = [{"title": "Alter Beschluss", "text": "Text", "date": "2019-05-01"}]

        stored = []
        with patch.object(Decision, "store", lambda self: stored.append(self)):
            results = list(import_decisions_from_records(records))

        assert results == [""]
        assert stored[0].page_id is None
        assert stored[0].build_natural_key() == "None:Alter Beschluss"

    def test_skips_invalid_records(self):
        records = [
            {"title": "", "text": "", "date": "2024-01-01"},
            {"title": "No date", "text": "Text", "date": ""},
            {"title": "Valid", "text": "Text", "date": "2024-01-01"},
        ]

        stored = []
        with patch.object(Decision, "store", lambda self: stored.append(self)):
            results = list(import_decisions_from_records(records))

        assert results[0] == "Record 1: Missing both title and text"
        assert results[1] == "Record 2: Missing date"
        assert results[2] == ""
        assert len(stored) == 1
