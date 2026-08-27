"""
test_m_comment_stripping.py

Regression coverage for the "regex reads commented-out M/DAX code" bug: a
query whose real, active source had been repointed (e.g. to a local
reference like `Source = DATA_TABLES_DIM`) but still had its OLD
PowerBI.Dataflows(...)/workspaceId/dataflowId/entity binding left behind as
a `//`-commented block was being resolved from that stale comment instead
of its real source, e.g. reporting "No Access - Dataflow Not Available"
against a dataflow that isn't even used anymore.
"""
from core import lineage_lib as ll


def test_line_comment_removed_but_active_code_kept():
    text = '//old = 1\nreal = 2'
    out = ll.strip_m_comments(text)
    assert "old" not in out
    assert "real = 2" in out


def test_block_comment_removed_without_merging_adjacent_tokens():
    text = 'Oracle.Database(/* comment */ #"Server", "DB")'
    out = ll.strip_m_comments(text)
    assert "comment" not in out
    assert out == 'Oracle.Database(  #"Server", "DB")'
    assert 'Server' in out and 'Database(' in out


def test_multiline_block_comment_preserves_line_count():
    text = "a\n/*\nfoo\nbar\n*/\nb"
    out = ll.strip_m_comments(text)
    assert "foo" not in out and "bar" not in out
    assert out.count("\n") == text.count("\n")


def test_double_slash_inside_string_literal_is_not_a_comment():
    text = 'Web.Contents("https://example.com/api")'
    out = ll.strip_m_comments(text)
    assert out == text


def test_doubled_quote_escape_inside_string_does_not_break_scanning():
    text = 'x = "He said ""hi""" // trailing comment\ny = 1'
    out = ll.strip_m_comments(text)
    assert 'He said ""hi""' in out
    assert "trailing comment" not in out
    assert "y = 1" in out


def test_none_and_non_string_input_are_returned_unchanged():
    assert ll.strip_m_comments(None) is None
    assert ll.strip_m_comments(123) == 123
    assert ll.strip_m_comments("") == ""


def test_commented_out_dataflow_binding_is_not_treated_as_direct():
    """Reproduces the real-world case: the old GUID-based dataflow binding is
    fully commented out, and the query's real source is a local reference."""
    query = '''let
        //Source = PowerBI.Dataflows(null),
        //#"5a98b0e7-ca03-4824-8bde-681b358a84c3" = Source{[workspaceId="5a98b0e7-ca03-4824-8bde-681b358a84c3"]}[Data],
        // #"0590a47e-419a-4278-b41e-534e6e601226" = #"5a98b0e7-ca03-4824-8bde-681b358a84c3"{[dataflowId="0590a47e-419a-4278-b41e-534e6e601226"]}[Data],
        //#"101_DIRECTION1" = #"0590a47e-419a-4278-b41e-534e6e601226"{[entity="101_DIRECTION"]}[Data],
        Source = DATA_TABLES_DIM,
        La_Table = Source{[entity="101_DIRECTION"]}[Data]
    in
        La_Table'''
    universe = ll.Universe({
        "101_DIRECTION_POLE": query,
        "DATA_TABLES_DIM": 'let Source = SharePoint.Files("https://contoso.sharepoint.com/sites/x") in Source',
    })
    direct, enumerators, unrecognized = ll.analyze_direct_dataflow_bindings(universe, {})

    assert "101_DIRECTION_POLE" not in direct
    assert "101_DIRECTION_POLE" not in enumerators
    # not "unrecognized" either - the connector call isn't active at all,
    # so it should behave as if this query had no connector reference.
    assert not any(u["query"] == "101_DIRECTION_POLE" for u in unrecognized)


def test_commented_out_oracle_call_does_not_shadow_real_sharepoint_source():
    text = '''let
        //Old = Oracle.Database("SRV1", [Query="select * from t"]),
        Source = SharePoint.Files("https://contoso.sharepoint.com/sites/x")
    in
        Source'''
    universe = ll.Universe({"T": text})
    connector = ll.detect_connector(universe.get("T"))
    assert connector == "SharePoint Excel/CSV"
