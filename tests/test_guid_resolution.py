"""Tests for GUID -> dataflow-name resolution:
  Fix 1 - mappings captured at export time are persisted to the GUID cache
  Fix 2 - a GUID-only dataflow is identified by the set of entities read from it
  Fix 3 - the Power BI online lookup service wrapper
plus the shared cache read/write/merge behaviour that ties them together.
"""
import json

import pytest

from core import lineage_lib as ll
from services import dataflow_export, guid_resolver

WS = "11111111-1111-1111-1111-111111111111"
DF = "22222222-2222-2222-2222-222222222222"
KEY = f"{WS}/{DF}"


def _dataflow(name, entities):
    return {
        "entities": {e: {"$type": "LocalEntity", "name": e} for e in entities},
        "queries": {},
        "path": f"{name}.json",
        "raw": {"name": name, "entities": [{"name": e} for e in entities]},
    }


def _guid_bound_query(entity=None):
    entity_step = f',\n    Ent = DF{{[entity="{entity}"]}}[Data]' if entity else ""
    return (
        "let\n"
        "    Source = PowerPlatform.Dataflows(null),\n"
        '    Workspaces = Source{[Id="Workspaces"]}[Data],\n'
        f'    WS = Workspaces{{[workspaceId="{WS}"]}}[Data],\n'
        f'    DF = WS{{[dataflowId="{DF}"]}}[Data]'
        f"{entity_step}\n"
        "in\n"
        "    DF"
    )


# --------------------------------------------------------------------------
# Cache storage
# --------------------------------------------------------------------------

def test_update_guid_cache_writes_and_merges(tmp_path):
    path = str(tmp_path / "cache.json")

    written = ll.update_guid_cache({KEY: ll.make_guid_cache_entry("My Dataflow", "My WS", "test")}, path=path)
    assert list(written) == [KEY.lower()]

    cache = ll.load_guid_cache(path)
    assert ll.guid_cache_dataflow_name(cache, KEY) == "My Dataflow"
    assert ll.guid_cache_workspace_name(cache, KEY) == "My WS"
    assert json.load(open(path, encoding="utf-8"))[KEY.lower()]["source"] == "test"


def test_update_guid_cache_does_not_clobber_existing_unless_asked(tmp_path):
    path = str(tmp_path / "cache.json")
    ll.update_guid_cache({KEY: "Original"}, path=path)

    assert ll.update_guid_cache({KEY: "Replacement"}, path=path) == {}
    assert ll.guid_cache_dataflow_name(ll.load_guid_cache(path), KEY) == "Original"

    ll.update_guid_cache({KEY: "Replacement"}, path=path, overwrite=True)
    assert ll.guid_cache_dataflow_name(ll.load_guid_cache(path), KEY) == "Replacement"


def test_guid_cache_lookup_is_case_insensitive(tmp_path):
    path = str(tmp_path / "cache.json")
    ll.update_guid_cache({KEY.upper(): "Upper Cased"}, path=path)
    cache = ll.load_guid_cache(path)

    assert ll.guid_cache_dataflow_name(cache, KEY.lower()) == "Upper Cased"
    assert ll.guid_cache_dataflow_name(cache, KEY.upper()) == "Upper Cased"


def test_update_guid_cache_ignores_empty_and_nameless_entries(tmp_path):
    path = str(tmp_path / "cache.json")
    assert ll.update_guid_cache({}, path=path) == {}
    assert ll.update_guid_cache({KEY: None, "": "x", "a/b": {"workspace_name": "no name"}}, path=path) == {}


def test_update_guid_cache_survives_a_corrupt_cache_file(tmp_path):
    path = str(tmp_path / "cache.json")
    open(path, "w", encoding="utf-8").write("{ not json")

    ll.update_guid_cache({KEY: "Recovered"}, path=path)
    assert ll.guid_cache_dataflow_name(ll.load_guid_cache(path), KEY) == "Recovered"


# --------------------------------------------------------------------------
# Fix 2 - identify a dataflow by its contents
# --------------------------------------------------------------------------

def test_fingerprint_matches_the_only_dataflow_holding_every_entity():
    dataflows = {
        "Statistique Support": _dataflow("Statistique Support",
                                          ["1_Incident", "Assignment_Group_Type", "Service", "999_PARAMETERS"]),
        "Finance": _dataflow("Finance", ["1_Incident", "999_PARAMETERS"]),
        "Other": _dataflow("Other", ["Service", "999_PARAMETERS"]),
    }
    stem, reason = ll.fingerprint_match_dataflow({"1_Incident", "Assignment_Group_Type", "Service"}, dataflows)

    assert stem == "Statistique Support"
    assert reason is None


def test_fingerprint_refuses_a_single_entity_match():
    """'999_PARAMETERS' alone exists in many dataflows - never guess from one name."""
    dataflows = {
        "A": _dataflow("A", ["999_PARAMETERS"]),
        "B": _dataflow("B", ["999_PARAMETERS"]),
    }
    stem, reason = ll.fingerprint_match_dataflow({"999_PARAMETERS"}, dataflows)

    assert stem is None
    assert "at least 2" in reason


def test_fingerprint_refuses_an_ambiguous_match():
    dataflows = {
        "A": _dataflow("A", ["X", "Y", "Z"]),
        "B": _dataflow("B", ["X", "Y", "W"]),
    }
    stem, reason = ll.fingerprint_match_dataflow({"X", "Y"}, dataflows)

    assert stem is None
    assert "ambiguous" in reason


def test_fingerprint_requires_all_entities_not_just_some():
    dataflows = {
        "A": _dataflow("A", ["X", "Y"]),
        "B": _dataflow("B", ["Elsewhere"]),
    }
    stem, reason = ll.fingerprint_match_dataflow({"X", "Y", "Elsewhere"}, dataflows)

    assert stem is None
    assert "no provided dataflow file contains all" in reason


def test_fingerprint_skips_dataflow_files_that_failed_to_load():
    dataflows = {
        "Broken": {"entities": {"X": {}, "Y": {}}, "queries": {}, "error": "JSON parse error"},
        "Good": _dataflow("Good", ["X", "Y"]),
    }
    stem, _ = ll.fingerprint_match_dataflow({"X", "Y"}, dataflows)

    assert stem == "Good"


def test_fingerprint_drops_a_name_that_exists_in_no_dataflow_at_all():
    """A token that is nowhere is more likely a dataflow name than an entity."""
    dataflows = {
        "A": _dataflow("A", ["X", "Y", "Z"]),
        "B": _dataflow("B", ["X"]),
    }
    stem, reason = ll.fingerprint_match_dataflow({"X", "Y", "Not An Entity Anywhere"}, dataflows)

    assert stem == "A"
    assert reason is None


def test_fingerprint_still_needs_two_real_entities_after_dropping():
    dataflows = {"A": _dataflow("A", ["X", "Y"])}
    stem, reason = ll.fingerprint_match_dataflow({"X", "Nowhere1", "Nowhere2"}, dataflows)

    assert stem is None
    assert "no provided dataflow file contains all" in reason


def test_fingerprint_still_refuses_ambiguity_after_dropping():
    dataflows = {
        "A": _dataflow("A", ["X", "Y"]),
        "B": _dataflow("B", ["X", "Y"]),
    }
    stem, reason = ll.fingerprint_match_dataflow({"X", "Y", "Nowhere"}, dataflows)

    assert stem is None
    assert "ambiguous" in reason


def test_collect_guid_entity_requests_gathers_direct_and_downstream_entities():
    universe = ll.Universe({
        "DF_SOURCE": _guid_bound_query("1_Incident"),
        "Service": 'let Source = #"DF_SOURCE", E = Source{[entity="Service"]}[Data] in E',
        "Unrelated": 'let Source = Excel.Workbook(File.Contents("c:\\x.xlsx")) in Source',
    })
    direct, _, _ = ll.analyze_direct_dataflow_bindings(universe, {})
    requests = ll.collect_guid_entity_requests(universe, direct)

    assert set(requests) == {KEY}
    assert requests[KEY]["entities"] == {"1_Incident", "Service"}
    assert requests[KEY]["workspace_id"] == WS
    assert requests[KEY]["dataflow_id"] == DF


def test_collect_guid_entity_requests_ignores_already_named_dataflows():
    universe = ll.Universe({
        "Named": 'let Source = PowerPlatform.Dataflows(null),'
                 ' D = Source{[workspaceName="WKS", dataflowName="DTF A"]}[Data],'
                 ' E = D{[entity="T1"]}[Data] in E',
    })
    direct, _, _ = ll.analyze_direct_dataflow_bindings(universe, {})

    assert ll.collect_guid_entity_requests(universe, direct) == {}


def test_resolve_guids_by_fingerprint_end_to_end():
    universe = ll.Universe({
        "DF_SOURCE": _guid_bound_query("1_Incident"),
        "Service": 'let Source = #"DF_SOURCE", E = Source{[entity="Service"]}[Data] in E',
    })
    direct, _, _ = ll.analyze_direct_dataflow_bindings(universe, {})
    dataflows = {
        "Stats file(1)": _dataflow("Dataflow DSI RUN Statistique Support", ["1_Incident", "Service", "Extra"]),
        "Decoy": _dataflow("Decoy", ["1_Incident"]),
    }
    resolved, unresolved = ll.resolve_guids_by_fingerprint(universe, direct, dataflows)

    # The dataflow's declared name is recorded, not the filename stem.
    assert resolved[KEY]["dataflow_name"] == "Dataflow DSI RUN Statistique Support"
    assert resolved[KEY]["source"] == "entity-fingerprint"
    assert unresolved == []


def test_resolve_guids_by_fingerprint_reports_why_it_failed():
    universe = ll.Universe({"DF_SOURCE": _guid_bound_query("Only_One")})
    direct, _, _ = ll.analyze_direct_dataflow_bindings(universe, {})

    resolved, unresolved = ll.resolve_guids_by_fingerprint(universe, direct, {"A": _dataflow("A", ["Only_One"])})

    assert resolved == {}
    assert unresolved[0]["key"] == KEY
    assert unresolved[0]["entities"] == ["Only_One"]
    assert "at least 2" in unresolved[0]["reason"]


def test_a_resolved_guid_makes_the_binding_report_the_real_name():
    universe = ll.Universe({"DF_SOURCE": _guid_bound_query("1_Incident")})
    cache = {KEY: {"dataflow_name": "Dataflow DSI RUN Statistique Support", "workspace_name": "WKS DTS"}}

    direct, _, _ = ll.analyze_direct_dataflow_bindings(universe, {}, cache)

    assert direct["DF_SOURCE"]["dataflow"] == "Dataflow DSI RUN Statistique Support"
    assert direct["DF_SOURCE"]["workspace"] == "WKS DTS"


def test_collect_reference_entity_guids_skips_known_ones():
    dataflows = {
        "A": {"entities": {"E1": {"$type": "ReferenceEntity", "modelId": KEY},
                            "E2": {"$type": "ReferenceEntity", "modelId": "ws2/df2"},
                            "E3": {"$type": "LocalEntity"}},
               "queries": {}},
    }
    pairs = ll.collect_reference_entity_guids(dataflows, {KEY.lower(): "Known"})

    assert set(pairs) == {"ws2/df2"}
    assert pairs["ws2/df2"] == {"workspace_id": "ws2", "dataflow_id": "df2"}


# --------------------------------------------------------------------------
# Fix 1 - export-time capture
# --------------------------------------------------------------------------

def test_persist_guid_mappings_writes_export_results(monkeypatch, tmp_path):
    path = str(tmp_path / "cache.json")
    monkeypatch.setattr(ll, "GUID_CACHE_PATH", path)

    saved = dataflow_export.persist_guid_mappings([
        {"key": KEY, "workspace_id": WS, "dataflow_id": DF,
         "dataflow_name": "DTF A", "workspace_name": "WKS DTS"},
    ])

    assert list(saved) == [KEY.lower()]
    cache = ll.load_guid_cache(path)
    assert ll.guid_cache_dataflow_name(cache, KEY) == "DTF A"
    assert ll.guid_cache_workspace_name(cache, KEY) == "WKS DTS"
    assert saved[KEY.lower()]["source"] == "powerbi-export"


def test_persist_guid_mappings_accepts_a_single_object(monkeypatch, tmp_path):
    """ConvertTo-Json collapses a one-item PowerShell array into an object."""
    path = str(tmp_path / "cache.json")
    monkeypatch.setattr(ll, "GUID_CACHE_PATH", path)

    saved = dataflow_export.persist_guid_mappings(
        {"workspace_id": WS, "dataflow_id": DF, "dataflow_name": "DTF A"})

    assert list(saved) == [KEY.lower()]


def test_persist_guid_mappings_tolerates_missing_or_junk_input(monkeypatch, tmp_path):
    monkeypatch.setattr(ll, "GUID_CACHE_PATH", str(tmp_path / "cache.json"))

    assert dataflow_export.persist_guid_mappings(None) == {}
    assert dataflow_export.persist_guid_mappings([]) == {}
    assert dataflow_export.persist_guid_mappings(["not a dict", {"dataflow_name": ""}]) == {}


def test_export_persists_mappings_and_reports_them(monkeypatch, tmp_path):
    path = str(tmp_path / "cache.json")
    monkeypatch.setattr(ll, "GUID_CACHE_PATH", path)

    payload = {
        "success": True, "stage": "done", "message": "1 of 1 dataflow(s) exported.",
        "files": [str(tmp_path / "A.json")],
        "mappings": [{"workspace_id": WS, "dataflow_id": DF, "dataflow_name": "DTF A",
                       "workspace_name": "WKS DTS"}],
    }

    class FakeProc:
        def __init__(self):
            self.stdout = iter(["Exporting 'DTF A' (id)...\n", "##RESULT##" + json.dumps(payload) + "\n"])

        def wait(self):
            pass

    monkeypatch.setattr(dataflow_export.subprocess, "Popen", lambda cmd, **kw: FakeProc())

    lines = []
    ok, result = dataflow_export.export_all_dataflows(WS, str(tmp_path), progress_cb=lines.append)

    assert ok is True
    assert any("Recorded 1 dataflow GUID name mapping" in line for line in lines)
    assert ll.guid_cache_dataflow_name(ll.load_guid_cache(path), KEY) == "DTF A"


def test_export_refreshes_a_renamed_dataflow(monkeypatch, tmp_path):
    """The service is authoritative, so an export overwrites a stale name."""
    path = str(tmp_path / "cache.json")
    monkeypatch.setattr(ll, "GUID_CACHE_PATH", path)
    ll.update_guid_cache({KEY: ll.make_guid_cache_entry("Old Name", source="entity-fingerprint")}, path=path)

    dataflow_export.persist_guid_mappings([{"workspace_id": WS, "dataflow_id": DF, "dataflow_name": "New Name"}])

    assert ll.guid_cache_dataflow_name(ll.load_guid_cache(path), KEY) == "New Name"


# --------------------------------------------------------------------------
# Fix 3 - online lookup
# --------------------------------------------------------------------------

class _FakeCompleted:
    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = 0


def test_online_lookup_persists_resolved_names(monkeypatch, tmp_path):
    path = str(tmp_path / "cache.json")
    monkeypatch.setattr(ll, "GUID_CACHE_PATH", path)
    payload = {
        "success": True, "stage": "done", "message": "1 of 1 dataflow GUID(s) resolved.",
        "mappings": [{"workspace_id": WS, "dataflow_id": DF, "dataflow_name": "DTF Online",
                       "workspace_name": "WKS DTS"}],
        "failures": [],
    }
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["pairs"] = json.load(open(cmd[cmd.index("-PairsFile") + 1], encoding="utf-8"))
        return _FakeCompleted("Looking up dataflow names...\n##RESULT##" + json.dumps(payload))

    monkeypatch.setattr(guid_resolver.subprocess, "run", fake_run)

    lines = []
    ok, result = guid_resolver.resolve_dataflow_names(
        [{"workspace_id": WS, "dataflow_id": DF}], progress_cb=lines.append)

    assert ok is True
    assert captured["pairs"] == [{"workspace_id": WS, "dataflow_id": DF}]
    assert lines == ["Looking up dataflow names..."]
    assert ll.guid_cache_dataflow_name(ll.load_guid_cache(path), KEY) == "DTF Online"
    assert result["mappings"][KEY.lower()]["source"] == "powerbi-lookup"


def test_online_lookup_deletes_its_temp_file(monkeypatch, tmp_path):
    monkeypatch.setattr(ll, "GUID_CACHE_PATH", str(tmp_path / "cache.json"))
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["pairs_file"] = cmd[cmd.index("-PairsFile") + 1]
        return _FakeCompleted('##RESULT##{"success": true, "mappings": [], "failures": []}')

    monkeypatch.setattr(guid_resolver.subprocess, "run", fake_run)
    guid_resolver.resolve_dataflow_names([{"workspace_id": WS, "dataflow_id": DF}])

    import os
    assert not os.path.exists(seen["pairs_file"])


def test_online_lookup_is_a_no_op_without_pairs(monkeypatch):
    def explode(*a, **kw):
        raise AssertionError("PowerShell must not be launched when there is nothing to look up")

    monkeypatch.setattr(guid_resolver.subprocess, "run", explode)

    assert guid_resolver.resolve_dataflow_names([])[0] is True
    assert guid_resolver.resolve_dataflow_names([{"workspace_id": WS}])[1]["mappings"] == {}


def test_online_lookup_surfaces_script_failures(monkeypatch, tmp_path):
    monkeypatch.setattr(ll, "GUID_CACHE_PATH", str(tmp_path / "cache.json"))
    payload = {"success": False, "stage": "auth", "message": "Sign-in failed or was cancelled: boom"}
    monkeypatch.setattr(guid_resolver.subprocess, "run",
                        lambda cmd, **kw: _FakeCompleted("##RESULT##" + json.dumps(payload)))

    ok, message = guid_resolver.resolve_dataflow_names([{"workspace_id": WS, "dataflow_id": DF}])

    assert ok is False
    assert "Sign-in failed" in message


def test_online_lookup_reports_a_crashed_script(monkeypatch, tmp_path):
    monkeypatch.setattr(ll, "GUID_CACHE_PATH", str(tmp_path / "cache.json"))
    monkeypatch.setattr(guid_resolver.subprocess, "run", lambda cmd, **kw: _FakeCompleted("kaboom"))

    ok, message = guid_resolver.resolve_dataflow_names([{"workspace_id": WS, "dataflow_id": DF}])

    assert ok is False
    assert "did not report a result" in message


def test_online_lookup_reports_a_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(ll, "GUID_CACHE_PATH", str(tmp_path / "cache.json"))

    def fake_run(cmd, **kwargs):
        raise guid_resolver.subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    monkeypatch.setattr(guid_resolver.subprocess, "run", fake_run)

    ok, message = guid_resolver.resolve_dataflow_names([{"workspace_id": WS, "dataflow_id": DF}], timeout_seconds=5)

    assert ok is False
    assert "timed out after 5 seconds" in message


# --------------------------------------------------------------------------
# Pipeline integration
# --------------------------------------------------------------------------

def test_pipeline_pass_learns_offline_and_skips_signing_in(monkeypatch, tmp_path):
    from reporting import lineage_report as blr

    monkeypatch.setattr(ll, "GUID_CACHE_PATH", str(tmp_path / "cache.json"))
    universe = ll.Universe({
        "DF_SOURCE": _guid_bound_query("1_Incident"),
        "Service": 'let Source = #"DF_SOURCE", E = Source{[entity="Service"]}[Data] in E',
    })
    direct, _, _ = ll.analyze_direct_dataflow_bindings(universe, {})
    dataflows = {"Stats": _dataflow("Dataflow DSI RUN Statistique Support", ["1_Incident", "Service"])}

    summary = blr._resolve_unknown_dataflow_guids(universe, direct, dataflows, {}, online_lookup=False)

    assert summary["learned"][KEY]["dataflow_name"] == "Dataflow DSI RUN Statistique Support"
    assert summary["unresolved"] == []
    # And the learned name is now visible to a fresh binding analysis.
    direct2, _, _ = ll.analyze_direct_dataflow_bindings(universe, {}, ll.load_guid_cache())
    assert direct2["DF_SOURCE"]["dataflow"] == "Dataflow DSI RUN Statistique Support"


def test_pipeline_pass_falls_back_to_the_online_lookup(monkeypatch, tmp_path):
    from reporting import lineage_report as blr

    monkeypatch.setattr(ll, "GUID_CACHE_PATH", str(tmp_path / "cache.json"))
    universe = ll.Universe({"DF_SOURCE": _guid_bound_query("Only_One")})
    direct, _, _ = ll.analyze_direct_dataflow_bindings(universe, {})
    asked = {}

    def fake_lookup(pairs, progress_cb=None):
        asked["pairs"] = pairs
        return True, {"mappings": {KEY.lower(): {"dataflow_name": "DTF Online"}}, "failures": [], "message": ""}

    monkeypatch.setattr(guid_resolver, "resolve_dataflow_names", fake_lookup)

    summary = blr._resolve_unknown_dataflow_guids(universe, direct, {}, {}, online_lookup=True)

    assert asked["pairs"] == [{"workspace_id": WS, "dataflow_id": DF}]
    assert summary["learned"][KEY.lower()]["dataflow_name"] == "DTF Online"
    assert summary["unresolved"] == []


def test_pipeline_pass_keeps_the_reason_when_nothing_identifies_the_guid(monkeypatch, tmp_path):
    from reporting import lineage_report as blr

    monkeypatch.setattr(ll, "GUID_CACHE_PATH", str(tmp_path / "cache.json"))
    universe = ll.Universe({"DF_SOURCE": _guid_bound_query("Only_One")})
    direct, _, _ = ll.analyze_direct_dataflow_bindings(universe, {})

    summary = blr._resolve_unknown_dataflow_guids(universe, direct, {}, {}, online_lookup=False)

    assert summary["learned"] == {}
    assert "at least 2" in summary["reason_by_key"][KEY]

    note = blr._guid_identification_note(direct["DF_SOURCE"], {"guid_resolution": summary})
    assert "Could not identify this dataflow" in note


def test_guid_identification_note_is_silent_for_named_dataflows():
    from reporting import lineage_report as blr

    assert blr._guid_identification_note({"dataflow": "DTF FINANCE 000"}, {"guid_resolution": {}}) is None
