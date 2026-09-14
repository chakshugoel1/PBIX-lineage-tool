"""Load and validate the requirement traceability workbook.

Fixed input contract (agreed design): a worksheet named ``Requirements`` with
the header in row 1. Mandatory columns: ``Requirement ID``, ``Title``,
``Status``; recommended: ``Description``, ``Priority``, ``Raised Date``;
optional: ``Business Owner``, ``Business Area``, ``Expected Change Date``,
``Seed Objects``, ``Notes``. Extra columns are ignored with a warning.

``load_requirements()`` is the only entry point most callers need: it returns
``(requirements, warnings)`` and raises :class:`RequirementsError` for
contract violations that make the file unusable (missing sheet, missing
mandatory column, empty mandatory cell, duplicate Requirement ID).
"""
import datetime

from openpyxl import load_workbook

SHEET_NAME = "Requirements"

MANDATORY_COLUMNS = ("Requirement ID", "Title", "Status")
RECOMMENDED_COLUMNS = ("Description", "Priority", "Raised Date")
OPTIONAL_COLUMNS = (
    "Business Owner", "Business Area", "Expected Change Date", "Seed Objects", "Notes",
)
KNOWN_COLUMNS = MANDATORY_COLUMNS + RECOMMENDED_COLUMNS + OPTIONAL_COLUMNS

STATUS_VALUES = ("Proposed", "Approved", "In Progress", "Done", "Cancelled")
INACTIVE_STATUSES = ("Done", "Cancelled")
PRIORITY_VALUES = ("Critical", "High", "Medium", "Low")
DEFAULT_PRIORITY = "Medium"

_FIELD_BY_COLUMN = {
    "Requirement ID": "id",
    "Title": "title",
    "Status": "status",
    "Description": "description",
    "Priority": "priority",
    "Raised Date": "raised_date",
    "Business Owner": "business_owner",
    "Business Area": "business_area",
    "Expected Change Date": "expected_change_date",
    "Seed Objects": "seed_objects",
    "Notes": "notes",
}
_DATE_FIELDS = ("raised_date", "expected_change_date")


class RequirementsError(ValueError):
    """The requirements workbook violates the fixed input contract."""


def load_requirements(path):
    """Read `path` and return ``(requirements, warnings)``.

    Each requirement is a plain dict with keys: id, title, status, active,
    description, priority, raised_date, business_owner, business_area,
    expected_change_date, seed_objects, notes, row_number.
    """
    warnings = []
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except RequirementsError:
        raise
    except Exception as exc:
        raise RequirementsError(f"Could not open requirements workbook: {exc}") from exc

    try:
        if SHEET_NAME not in workbook.sheetnames:
            found = ", ".join(workbook.sheetnames) or "(none)"
            raise RequirementsError(
                f"Worksheet '{SHEET_NAME}' not found in {path} (sheets found: {found})."
            )
        sheet = workbook[SHEET_NAME]
        rows_iter = sheet.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            raise RequirementsError(f"Worksheet '{SHEET_NAME}' is empty - no header row found.")

        headers = [str(cell).strip() if cell is not None else "" for cell in header_row]
        index = _map_headers(headers, warnings)

        requirements = []
        seen_ids = {}
        for row_number, row in enumerate(rows_iter, start=2):
            values = {field: _cell(row, idx) for field, idx in index.items()}
            if not any(values.values()):
                continue  # fully blank row
            requirement = _build_requirement(values, row_number, warnings)
            folded = requirement["id"].casefold()
            if folded in seen_ids:
                raise RequirementsError(
                    f"Duplicate Requirement ID '{requirement['id']}' "
                    f"(rows {seen_ids[folded]} and {row_number}). Requirement IDs must be unique."
                )
            seen_ids[folded] = row_number
            requirements.append(requirement)
    finally:
        workbook.close()

    if not requirements:
        warnings.append(f"Worksheet '{SHEET_NAME}' contains no requirement rows.")
    return requirements, warnings


def _map_headers(headers, warnings):
    """Map field name -> column index; validate the fixed contract."""
    index = {}
    for position, header in enumerate(headers):
        if not header:
            continue
        if header in _FIELD_BY_COLUMN:
            index[_FIELD_BY_COLUMN[header]] = position
        else:
            warnings.append(f"Unknown column '{header}' ignored.")

    missing_mandatory = [
        column for column in MANDATORY_COLUMNS if _FIELD_BY_COLUMN[column] not in index
    ]
    if missing_mandatory:
        raise RequirementsError(
            "Missing mandatory column(s): " + ", ".join(missing_mandatory) + "."
        )
    for column in RECOMMENDED_COLUMNS:
        if _FIELD_BY_COLUMN[column] not in index:
            warnings.append(
                f"Recommended column '{column}' missing - related analysis will be degraded."
            )
    return index


def _cell(row, position):
    value = row[position] if position < len(row) else None
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value).strip()


def _build_requirement(values, row_number, warnings):
    for field in ("id", "title", "status"):
        if not values.get(field):
            column = next(c for c, f in _FIELD_BY_COLUMN.items() if f == field)
            raise RequirementsError(
                f"Row {row_number}: mandatory column '{column}' is empty."
            )

    status = _normalize_choice(
        values["status"], STATUS_VALUES, f"Row {row_number} Status", warnings,
    )
    priority = _normalize_choice(
        values.get("priority") or DEFAULT_PRIORITY, PRIORITY_VALUES,
        f"Row {row_number} Priority", warnings,
    )
    if priority not in PRIORITY_VALUES:
        priority = DEFAULT_PRIORITY

    requirement = {
        "id": values["id"],
        "title": values["title"],
        "status": status,
        "active": status not in INACTIVE_STATUSES,
        "description": values.get("description", ""),
        "priority": priority,
        "raised_date": _date_text(values.get("raised_date"), row_number, "Raised Date", warnings),
        "business_owner": values.get("business_owner", ""),
        "business_area": values.get("business_area", ""),
        "expected_change_date": _date_text(
            values.get("expected_change_date"), row_number, "Expected Change Date", warnings,
        ),
        "seed_objects": values.get("seed_objects", ""),
        "notes": values.get("notes", ""),
        "row_number": row_number,
    }
    return requirement


def _normalize_choice(value, allowed, label, warnings):
    for candidate in allowed:
        if value.casefold() == candidate.casefold():
            return candidate
    warnings.append(f"{label}: '{value}' is not a recognized value {list(allowed)} - kept as-is.")
    return value


def _date_text(value, row_number, column, warnings):
    if not value:
        return ""
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        warnings.append(f"Row {row_number} {column}: '{value}' is not a YYYY-MM-DD date - ignored.")
        return ""
