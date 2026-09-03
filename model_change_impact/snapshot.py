"""
snapshot.py

Read-only extraction of a single PBIX's semantic model (measures, tables,
columns, calculated columns, relationships) into a plain, JSON-serializable
snapshot dict. This is generic across ANY PBIX - nothing here assumes a
specific model's table/column/measure names, so the same code works for
whatever baseline/changed file pair a user picks in the GUI.

`build_snapshot()` is the only entry point most callers need.
"""
import datetime
import os

import pandas as pd
from pbixray import PBIXRay

# Tabular Object Model DataType enum (Microsoft.AnalysisServices.Tabular.DataType) -
# tmschema_columns exposes only the raw numeric code, so map it to a readable
# name; unmapped codes are kept as-is (prefixed) rather than guessed.
_DATA_TYPE_NAMES = {
    1: "Automatic", 2: "String", 6: "Int64", 8: "Double",
    9: "DateTime", 10: "Decimal", 11: "Boolean", 17: "Binary", 19: "Unknown",
}


def _data_type_name(code):
    if code is None:
        return None
    try:
        return _DATA_TYPE_NAMES.get(int(code), f"Unrecognized({code})")
    except (TypeError, ValueError):
        return f"Unrecognized({code})"


def _has_col(df, col):
    """True if `df` is a real DataFrame exposing `col` (pbixray returns a
    columnless DataFrame - not just an empty one - when a given metadata
    table is entirely absent from a PBIX)."""
    return col in getattr(df, "columns", [])


def _val(row, col, default=None):
    """Safe cell access: missing column, None, or NaN all fall back to
    `default` instead of leaking pandas-isms into the snapshot."""
    if col not in row:
        return default
    value = row[col]
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    return value


def _measure_raw_sql_enrichment(model):
    """Best-effort: pull FormatString/IsHidden/KPIID/LineageTag for measures
    via pbixray's internal raw-SQL interface. pbixray's public
    `dax_measures` attribute only exposes Name/Expression/DisplayFolder/
    Description, even though the underlying `Measure` table has more.
    Returns {(table, measure_name): {...}}, or {} if the internal interface
    isn't available (this is undocumented pbixray internals, so degrade
    gracefully across pbixray versions instead of hard-failing)."""
    try:
        db = model._metadata.source._db
        df = db.query(
            "SELECT t.Name AS TableName, m.Name AS MeasureName, "
            "m.FormatString, m.IsHidden, m.KPIID, m.LineageTag "
            "FROM Measure m JOIN [Table] t ON m.TableID = t.ID;"
        )
    except Exception:
        return {}
    if not _has_col(df, "TableName"):
        return {}
    out = {}
    for _, row in df.iterrows():
        out[(str(row["TableName"]), str(row["MeasureName"]))] = {
            "format_string": _val(row, "FormatString"),
            "is_hidden": bool(_val(row, "IsHidden", False)),
            "kpi_id": _val(row, "KPIID"),
            "lineage_tag": _val(row, "LineageTag"),
        }
    return out


def _column_enrichment(model):
    """Best-effort per-(table, column) enrichment from the public
    `tmschema_columns` attribute - richer than `schema`/`dax_columns` alone
    (adds FormatString/IsHidden/DataType/Description/LineageTag/
    DisplayFolder). Returns {} if unavailable/empty for this PBIX."""
    try:
        df = model.tmschema_columns
    except Exception:
        return {}
    if not _has_col(df, "TableName") or not _has_col(df, "Name"):
        return {}
    out = {}
    for _, row in df.iterrows():
        out[(str(row["TableName"]), str(row["Name"]))] = {
            "data_type": _data_type_name(_val(row, "DataType")),
            "format_string": _val(row, "FormatString"),
            "is_hidden": bool(_val(row, "IsHidden", False)),
            "description": _val(row, "Description"),
            "display_folder": _val(row, "DisplayFolder"),
            "lineage_tag": _val(row, "LineageTag"),
        }
    return out


def _table_enrichment(model):
    """Best-effort per-table enrichment (IsHidden/Description/LineageTag)
    from the public `tmschema_tables` attribute."""
    try:
        df = model.tmschema_tables
    except Exception:
        return {}
    if not _has_col(df, "Name"):
        return {}
    out = {}
    for _, row in df.iterrows():
        out[str(row["Name"])] = {
            "is_hidden": bool(_val(row, "IsHidden", False)),
            "description": _val(row, "Description"),
            "lineage_tag": _val(row, "LineageTag"),
        }
    return out


def build_snapshot(pbix_path):
    """Read `pbix_path` and return a JSON-serializable snapshot dict. Closes
    the underlying pbixray model when done (these files can be large)."""
    model = PBIXRay(pbix_path)
    try:
        return _build_snapshot_from_model(model, pbix_path)
    finally:
        close = getattr(model, "close", None)
        if callable(close):
            close()


def _build_snapshot_from_model(model, pbix_path):
    measure_extra = _measure_raw_sql_enrichment(model)
    column_extra = _column_enrichment(model)
    table_extra = _table_enrichment(model)

    calculated_table_names = set()
    if _has_col(model.dax_tables, "TableName"):
        calculated_table_names = {str(t) for t in model.dax_tables["TableName"]}

    calculated_column_expr = {}
    if _has_col(model.dax_columns, "TableName") and _has_col(model.dax_columns, "ColumnName"):
        for _, row in model.dax_columns.iterrows():
            key = (str(row["TableName"]), str(row["ColumnName"]))
            calculated_column_expr[key] = _val(row, "Expression", "")

    m_expression_by_table = {}
    if _has_col(model.power_query, "TableName") and _has_col(model.power_query, "Expression"):
        for _, row in model.power_query.iterrows():
            m_expression_by_table[str(row["TableName"])] = _val(row, "Expression", "")

    tables = _build_tables(model, calculated_table_names, calculated_column_expr,
                            m_expression_by_table, column_extra, table_extra)
    measures = _build_measures(model, measure_extra)
    calculated_columns = _extract_calculated_columns(tables)
    relationships = _build_relationships(model)

    return {
        "source_file": os.path.basename(pbix_path),
        "extracted_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "tables": tables,
        "measures": measures,
        "calculated_columns": calculated_columns,
        "relationships": relationships,
    }


def _build_tables(model, calculated_table_names, calculated_column_expr,
                   m_expression_by_table, column_extra, table_extra):
    tables = {}
    if _has_col(model.schema, "TableName"):
        for table_name, group in model.schema.groupby("TableName"):
            table_name = str(table_name)
            columns = []
            for _, row in group.iterrows():
                col_name = str(row["ColumnName"])
                key = (table_name, col_name)
                extra = column_extra.get(key, {})
                is_calculated = key in calculated_column_expr
                columns.append({
                    "name": col_name,
                    "data_type": extra.get("data_type") or _val(row, "PandasDataType", ""),
                    "is_calculated": is_calculated,
                    "expression": calculated_column_expr.get(key) if is_calculated else None,
                    "format_string": extra.get("format_string"),
                    "is_hidden": extra.get("is_hidden", False),
                    "description": extra.get("description"),
                    "display_folder": extra.get("display_folder"),
                    "lineage_tag": extra.get("lineage_tag"),
                })
            columns.sort(key=lambda c: c["name"])
            t_extra = table_extra.get(table_name, {})
            tables[table_name] = {
                "is_calculated_table": table_name in calculated_table_names,
                "m_expression": m_expression_by_table.get(table_name),
                "is_hidden": t_extra.get("is_hidden", False),
                "description": t_extra.get("description"),
                "lineage_tag": t_extra.get("lineage_tag"),
                "columns": columns,
            }
    else:
        # Extreme fallback (no per-column schema at all): still list the
        # table names so an all-tables-removed diff isn't silently missed.
        for table_name in model.tables:
            table_name = str(table_name)
            tables[table_name] = {
                "is_calculated_table": table_name in calculated_table_names,
                "m_expression": m_expression_by_table.get(table_name),
                "is_hidden": False,
                "description": None,
                "lineage_tag": None,
                "columns": [],
            }
    return tables


def _build_measures(model, measure_extra):
    measures = {}
    if _has_col(model.dax_measures, "TableName"):
        for _, row in model.dax_measures.iterrows():
            table_name = str(row["TableName"])
            measure_name = str(row["Name"])
            extra = measure_extra.get((table_name, measure_name), {})
            measures.setdefault(table_name, {})[measure_name] = {
                "expression": _val(row, "Expression", ""),
                "display_folder": _val(row, "DisplayFolder", ""),
                "description": _val(row, "Description", ""),
                "format_string": extra.get("format_string"),
                "is_hidden": extra.get("is_hidden", False),
                "lineage_tag": extra.get("lineage_tag"),
                "kpi_id": extra.get("kpi_id"),
            }
    return measures


def _extract_calculated_columns(tables):
    calculated_columns = [
        {
            "table": table_name,
            "name": col["name"],
            "expression": col["expression"],
            "data_type": col["data_type"],
            "format_string": col["format_string"],
            "lineage_tag": col["lineage_tag"],
        }
        for table_name, table in tables.items()
        for col in table["columns"]
        if col["is_calculated"]
    ]
    calculated_columns.sort(key=lambda c: (c["table"], c["name"]))
    return calculated_columns


def _build_relationships(model):
    relationships = []
    if _has_col(model.relationships, "FromTableName"):
        for _, row in model.relationships.iterrows():
            relationships.append({
                "from_table": _val(row, "FromTableName", ""),
                "from_column": _val(row, "FromColumnName", ""),
                "to_table": _val(row, "ToTableName", ""),
                "to_column": _val(row, "ToColumnName", ""),
                "is_active": bool(_val(row, "IsActive", True)),
                "cardinality": _val(row, "Cardinality", ""),
                "cross_filtering_behavior": _val(row, "CrossFilteringBehavior", ""),
                "rely_on_referential_integrity": bool(_val(row, "RelyOnReferentialIntegrity", False)),
            })
        relationships.sort(key=lambda r: (r["from_table"], r["from_column"], r["to_table"], r["to_column"]))
    return relationships
