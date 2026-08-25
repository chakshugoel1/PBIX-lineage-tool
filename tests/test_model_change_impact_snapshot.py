"""
test_model_change_impact_snapshot.py

Tests for model_change_impact/snapshot.py using a synthetic fake in place of
pbixray.PBIXRay (no real proprietary PBIX file is needed/committed - mirrors
this repo's existing test convention of not depending on real sample PBIX
files). Covers both the fully-enriched path (tmschema_* + raw-SQL measure
enrichment available) and the degraded-fallback path (older/newer pbixray
where those internals aren't available).
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_change_impact import snapshot


class _FakeDb:
    def __init__(self, measures_df):
        self._measures_df = measures_df

    def query(self, sql):
        return self._measures_df


class _FakeSource:
    def __init__(self, measures_df):
        self._db = _FakeDb(measures_df)


class _FakeMetadata:
    def __init__(self, measures_df):
        self.source = _FakeSource(measures_df)


class FakeModel:
    """Stands in for pbixray.PBIXRay. Only the attributes snapshot.py
    touches are defined; enrichment attrs are omitted entirely in the
    "fallback" tests to simulate them being unavailable."""

    def __init__(self, *, schema, dax_measures, dax_columns, dax_tables,
                 relationships, power_query, tables=(),
                 tmschema_columns=None, tmschema_tables=None,
                 raw_measure_sql=None):
        self.schema = schema
        self.dax_measures = dax_measures
        self.dax_columns = dax_columns
        self.dax_tables = dax_tables
        self.relationships = relationships
        self.power_query = power_query
        self.tables = tables
        self.closed = False
        if tmschema_columns is not None:
            self.tmschema_columns = tmschema_columns
        if tmschema_tables is not None:
            self.tmschema_tables = tmschema_tables
        if raw_measure_sql is not None:
            self._metadata = _FakeMetadata(raw_measure_sql)

    def close(self):
        self.closed = True


def _base_frames():
    schema = pd.DataFrame([
        {"TableName": "Sales", "ColumnName": "OrderID", "PandasDataType": "Int64"},
        {"TableName": "Sales", "ColumnName": "Amount", "PandasDataType": "double"},
        {"TableName": "Sales", "ColumnName": "Margin", "PandasDataType": "double"},
        {"TableName": "Orders", "ColumnName": "OrderID", "PandasDataType": "Int64"},
    ])
    dax_measures = pd.DataFrame([
        {"TableName": "Sales", "Name": "Total Sales", "Expression": "SUM(Sales[Amount])",
         "DisplayFolder": "Revenue", "Description": "Total sales amount"},
    ])
    dax_columns = pd.DataFrame([
        {"TableName": "Sales", "ColumnName": "Margin", "Expression": "Sales[Amount] * 0.1"},
    ])
    dax_tables = pd.DataFrame([
        {"TableName": "DateCalc", "Expression": "CALENDAR(DATE(2024,1,1), DATE(2024,12,31))"},
    ])
    relationships = pd.DataFrame([
        {"FromTableName": "Sales", "FromColumnName": "OrderID", "ToTableName": "Orders",
         "ToColumnName": "OrderID", "IsActive": 1, "Cardinality": "M:1",
         "CrossFilteringBehavior": "Single", "RelyOnReferentialIntegrity": 0},
    ])
    power_query = pd.DataFrame([
        {"TableName": "Sales", "Expression": "let Source = Sql.Database(...) in Source"},
    ])
    return dict(schema=schema, dax_measures=dax_measures, dax_columns=dax_columns,
                dax_tables=dax_tables, relationships=relationships, power_query=power_query)


def _patch_pbixray(monkeypatch, fake_model):
    monkeypatch.setattr(snapshot, "PBIXRay", lambda path: fake_model)


class TestSnapshotFullyEnriched:
    def _make_model(self):
        frames = _base_frames()
        tmschema_columns = pd.DataFrame([
            {"TableName": "Sales", "Name": "OrderID", "DataType": "Int64", "FormatString": None,
             "IsHidden": 0, "Description": None, "DisplayFolder": None, "LineageTag": "col-orderid"},
            {"TableName": "Sales", "Name": "Amount", "DataType": "Double", "FormatString": "#,0.00",
             "IsHidden": 0, "Description": "Order amount", "DisplayFolder": None, "LineageTag": "col-amount"},
            {"TableName": "Sales", "Name": "Margin", "DataType": "Double", "FormatString": "#,0.00",
             "IsHidden": 1, "Description": None, "DisplayFolder": "Calcs", "LineageTag": "col-margin"},
            {"TableName": "Orders", "Name": "OrderID", "DataType": "Int64", "FormatString": None,
             "IsHidden": 0, "Description": None, "DisplayFolder": None, "LineageTag": "col-ordersid"},
        ])
        tmschema_tables = pd.DataFrame([
            {"Name": "Sales", "IsHidden": 0, "Description": "Fact table", "LineageTag": "tbl-sales"},
            {"Name": "Orders", "IsHidden": 0, "Description": None, "LineageTag": "tbl-orders"},
        ])
        raw_measure_sql = pd.DataFrame([
            {"TableName": "Sales", "MeasureName": "Total Sales", "FormatString": "$#,0",
             "IsHidden": 0, "KPIID": 0, "LineageTag": "measure-total-sales"},
        ])
        return FakeModel(tmschema_columns=tmschema_columns, tmschema_tables=tmschema_tables,
                          raw_measure_sql=raw_measure_sql, **frames)

    def test_measures_get_format_string_and_lineage_tag(self, monkeypatch):
        model = self._make_model()
        _patch_pbixray(monkeypatch, model)

        snap = snapshot.build_snapshot("fake.pbix")

        measure = snap["measures"]["Sales"]["Total Sales"]
        assert measure["expression"] == "SUM(Sales[Amount])"
        assert measure["format_string"] == "$#,0"
        assert measure["lineage_tag"] == "measure-total-sales"
        assert measure["is_hidden"] is False

    def test_calculated_column_is_flagged_and_enriched(self, monkeypatch):
        model = self._make_model()
        _patch_pbixray(monkeypatch, model)

        snap = snapshot.build_snapshot("fake.pbix")

        sales_columns = {c["name"]: c for c in snap["tables"]["Sales"]["columns"]}
        assert sales_columns["Margin"]["is_calculated"] is True
        assert sales_columns["Margin"]["expression"] == "Sales[Amount] * 0.1"
        assert sales_columns["Margin"]["format_string"] == "#,0.00"
        assert sales_columns["Margin"]["is_hidden"] is True
        assert sales_columns["Amount"]["is_calculated"] is False

        calc_cols = snap["calculated_columns"]
        assert len(calc_cols) == 1
        assert calc_cols[0] == {
            "table": "Sales", "name": "Margin", "expression": "Sales[Amount] * 0.1",
            "data_type": "Double", "format_string": "#,0.00", "lineage_tag": "col-margin",
        }

    def test_calculated_table_flagged_and_m_expression_attached(self, monkeypatch):
        model = self._make_model()
        _patch_pbixray(monkeypatch, model)

        snap = snapshot.build_snapshot("fake.pbix")

        assert snap["tables"]["Sales"]["is_calculated_table"] is False
        assert snap["tables"]["Sales"]["m_expression"].startswith("let Source")
        assert snap["tables"]["Orders"]["m_expression"] is None

    def test_relationships_extracted(self, monkeypatch):
        model = self._make_model()
        _patch_pbixray(monkeypatch, model)

        snap = snapshot.build_snapshot("fake.pbix")

        assert snap["relationships"] == [{
            "from_table": "Sales", "from_column": "OrderID",
            "to_table": "Orders", "to_column": "OrderID",
            "is_active": True, "cardinality": "M:1",
            "cross_filtering_behavior": "Single", "rely_on_referential_integrity": False,
        }]

    def test_model_closed_after_snapshot(self, monkeypatch):
        model = self._make_model()
        _patch_pbixray(monkeypatch, model)

        snapshot.build_snapshot("fake.pbix")

        assert model.closed is True


class TestSnapshotFallbackWhenEnrichmentUnavailable:
    """Older/newer pbixray versions might lack tmschema_columns/tmschema_tables
    or the internal raw-SQL interface - snapshot() must still work, just
    with fewer enriched fields."""

    def _make_model(self):
        frames = _base_frames()
        return FakeModel(**frames)  # no tmschema_*/raw_measure_sql attrs at all

    def test_measure_still_extracted_without_enrichment(self, monkeypatch):
        model = self._make_model()
        _patch_pbixray(monkeypatch, model)

        snap = snapshot.build_snapshot("fake.pbix")

        measure = snap["measures"]["Sales"]["Total Sales"]
        assert measure["expression"] == "SUM(Sales[Amount])"
        assert measure["format_string"] is None
        assert measure["lineage_tag"] is None
        assert measure["is_hidden"] is False

    def test_column_falls_back_to_schema_data_type(self, monkeypatch):
        model = self._make_model()
        _patch_pbixray(monkeypatch, model)

        snap = snapshot.build_snapshot("fake.pbix")

        sales_columns = {c["name"]: c for c in snap["tables"]["Sales"]["columns"]}
        assert sales_columns["Amount"]["data_type"] == "double"
        assert sales_columns["Amount"]["format_string"] is None
        assert sales_columns["Margin"]["is_calculated"] is True


class TestSnapshotEmptyModel:
    """No measures/relationships/calculated objects at all - should not
    crash, should just produce empty collections."""

    def test_empty_frames_produce_empty_collections(self, monkeypatch):
        empty = pd.DataFrame()
        model = FakeModel(
            schema=pd.DataFrame([{"TableName": "Lonely", "ColumnName": "ID", "PandasDataType": "Int64"}]),
            dax_measures=empty, dax_columns=empty, dax_tables=empty,
            relationships=empty, power_query=empty, tables=["Lonely"],
        )
        _patch_pbixray(monkeypatch, model)

        snap = snapshot.build_snapshot("fake.pbix")

        assert snap["measures"] == {}
        assert snap["relationships"] == []
        assert snap["calculated_columns"] == []
        assert snap["tables"]["Lonely"]["columns"][0]["name"] == "ID"
        assert snap["tables"]["Lonely"]["is_calculated_table"] is False
