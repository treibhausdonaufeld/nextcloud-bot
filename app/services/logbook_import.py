from typing import Any, Generator

from pandas import DataFrame

from app.models.decision import Decision

# Fields accepted from a CouchDB decision export
# (see scripts/export_decisions_couchdb.py)
EXPORT_FIELDS = (
    "title",
    "text",
    "date",
    "group_name",
    "valid_until",
    "objections",
    "external_link",
)


def import_decisions_from_records(records: list[dict[str, Any]]) -> Generator[str]:
    """
    Import decisions from a CouchDB export (list of dicts).

    Records keep their `page_id`, so the Decision natural key matches what
    protocol re-parsing produces: importing is an upsert and a later
    `sync --update-all` replaces page-bound decisions instead of
    duplicating them.

    Yields:
        str: Empty string on successful record import, error message on failure
    """
    for idx, record in enumerate(records):
        row_num = idx + 1
        try:
            decision_data = {
                field: str(record.get(field) or "") for field in EXPORT_FIELDS
            }

            if not decision_data["title"] and not decision_data["text"]:
                yield f"Record {row_num}: Missing both title and text"
                continue

            if not decision_data["date"]:
                yield f"Record {row_num}: Missing date"
                continue

            page_id = record.get("page_id")
            decision = Decision(
                page_id=int(page_id) if page_id else None,
                **decision_data,  # type: ignore[arg-type]
            )
            decision.store()

            yield ""
        except Exception as e:
            yield f"Record {row_num}: {str(e)}"


def import_decisions_from_excel(df: DataFrame) -> Generator[str]:
    """
    Import decisions from Excel file.

    Yields:
        str: Empty string on successful row import, error message on failure
    """

    # Expected columns mapping (flexible column names)
    expected_columns = {
        "title": ["Beschluss-Titel"],
        "text": ["Beschlusstext"],
        "date": ["Beschlussdatum"],
        "group_name": ["Kategorie"],
        "valid_until": ["Gültig bis"],
        "objections": ["Einwände"],
        "external_link": ["Link zum Protokoll"],
    }

    # Map actual column names to expected fields
    column_mapping = {}
    for field, possible_names in expected_columns.items():
        for col in df.columns:
            if col in possible_names:
                column_mapping[field] = col
                break

    for row_idx in range(len(df)):
        try:
            row_num = row_idx + 1
            row = df.iloc[row_idx]
            decision_data = {}

            # Map columns to Decision fields
            for field, excel_col in column_mapping.items():
                if excel_col in df.columns:
                    value = row[excel_col]
                    if (
                        value is not None
                        and str(value).strip() != ""
                        and str(value) != "nan"
                    ):
                        decision_data[field] = str(value)
                    else:
                        decision_data[field] = ""
                else:
                    decision_data[field] = ""

            # Ensure required fields
            if not decision_data.get("title") and not decision_data.get("text"):
                yield f"Row {row_num}: Missing both title and text"
                continue

            if not decision_data.get("date"):
                yield f"Row {row_num}: Missing date"
                continue

            decision_data["group_name"] = decision_data.get("group_name", "").split(
                " - "
            )[-1]

            # Create and save the decision
            decision = Decision(**decision_data)  # type: ignore[arg-type]
            decision.store()

            # Yield empty string on success
            yield ""

        except Exception as e:
            row_num = row_idx + 1
            yield f"Row {row_num}: {str(e)}"
