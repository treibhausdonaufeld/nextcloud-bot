import pandas as pd

from lib.nextcloud.models.decision import Decision


def import_decisions_from_excel(uploaded_file) -> tuple[int, list[str]]:
    # Read the Excel file
    df = pd.read_excel(uploaded_file)

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

    created_count = 0
    errors = []

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
                errors.append(f"Row {row_num}: Missing both title and text")
                continue

            if not decision_data.get("date"):
                errors.append(f"Row {row_num}: Missing date")
                continue

            decision_data["group_name"] = decision_data.get("group_name", "").split(
                " - "
            )[-1]

            # Create and save the decision
            decision = Decision(**decision_data)
            decision.save()
            created_count += 1
        except Exception as e:
            row_num = row_idx + 1
            errors.append(f"Row {row_num}: {str(e)}")

    return created_count, errors
