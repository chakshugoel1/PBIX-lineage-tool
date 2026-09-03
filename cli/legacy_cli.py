#!/usr/bin/env python
"""
legacy_cli.py

Two small standalone, read-only inspection tools kept around from before
lineage_lib.py's full pipeline existed - useful for a quick one-off look at
a PBIX file's dataflow references without running the full report pipeline
(build_lineage_report.py). Not part of the main pipeline and not imported by
it; each subcommand is independent.

Usage:
    python legacy_cli.py names "path\\to\\report.pbix" [--csv out.csv]
        List every workspace/dataflow name pair referenced in the PBIX's M
        code (literal, parametrised, or GUID-bound).

    python legacy_cli.py lineage "path\\to\\report.pbix" [--csv out.csv] [--table NAME]
        For every table/query in the model, trace which dataflow (and
        entity within it) it was ultimately loaded from - stops at the
        dataflow level, does not resolve the dataflow's own physical source
        (use build_lineage_report.py for that).

Requires: pip install pbixray
"""

import argparse
import csv
import re
import sys
from collections import defaultdict

try:
    from pbixray import PBIXRay
except ImportError:
    sys.exit("Missing dependency. Install with:  pip install pbixray")

from core import lineage_lib as ll

# workspaceName/dataflowName/workspaceId/dataflowId/entity extraction and the
# connector-call detector live in lineage_lib.py (ll.field_values /
# ll.first_field_value / ll.RE_USES_DATAFLOW_CONNECTOR) so these standalone
# tools can't drift out of sync with the main pipeline's parsing logic.
RE_LOCAL_ASSIGN = re.compile(r'\b([A-Za-z_][A-Za-z0-9_\-]*)\s*=\s*"((?:[^"\\]|\\.)*)"')
RE_SIMPLE_QUOTED = re.compile(r'^"((?:[^"\\]|\\.)*)"')


def build_global_param_values(entries):
    """Map parameter/query name -> its resolved literal string value (if any)."""
    values = {}
    for name, expr in entries:
        if expr is None:
            continue
        m = RE_SIMPLE_QUOTED.match(str(expr).strip())
        if m:
            values[name] = m.group(1)
    return values


def load_entries(pbix_path):
    model = PBIXRay(pbix_path)
    entries = []  # (source_kind, name, expression_text)
    for _, row in model.power_query.iterrows():
        entries.append(("table", row["TableName"], ll.strip_m_comments(str(row["Expression"]))))
    for _, row in model.m_parameters.iterrows():
        entries.append(("query", row["ParameterName"], ll.strip_m_comments(str(row["Expression"]))))
    return entries


# --------------------------------------------------------------------------
# "names" subcommand: list workspace/dataflow name pairs referenced in a PBIX
# --------------------------------------------------------------------------

def _names_resolve_token(token, local_map, global_map):
    """Resolve a captured token to a literal string, following local then
    global scope. Returns (value, resolution_kind) - the kind is reported in
    this subcommand's output, unlike the lineage subcommand below."""
    if token.startswith('"'):
        return ll.unquote(token), "literal"
    if token in local_map:
        return local_map[token], "local-variable"
    if token in global_map:
        return global_map[token], "parameter"
    return None, "unresolved"


def extract_names(pbix_path):
    entries = load_entries(pbix_path)
    global_params = build_global_param_values([(n, e) for _, n, e in entries])

    # --- Pass 1: entries that call the dataflow connector directly ------
    direct_hits = []  # (source_kind, name, text, local_map, ws_tokens, df_tokens, ws_ids, df_ids)
    enumerators = {}  # query_name -> resolved workspace value (queries that list all
                       # dataflows of one workspace, with no dataflow-name filter yet)
    for source_kind, name, text in entries:
        if not ll.RE_USES_DATAFLOW_CONNECTOR.search(text):
            continue

        local_map = {m.group(1): m.group(2) for m in RE_LOCAL_ASSIGN.finditer(text)}
        ws_tokens = ll.field_values(text, "workspaceName")
        df_tokens = ll.field_values(text, "dataflowName")
        ws_ids = [ll.unquote(t) for t in ll.field_values(text, "workspaceId")]
        df_ids = [ll.unquote(t) for t in ll.field_values(text, "dataflowId")]
        direct_hits.append((source_kind, name, text, local_map, ws_tokens, df_tokens, ws_ids, df_ids))

        if ws_tokens and not df_tokens and not ws_ids and not df_ids:
            ws_val, _ = _names_resolve_token(ws_tokens[0], local_map, global_params)
            enumerators[name] = ws_val

    results = []

    def add_result(source_kind, name, ws_val, ws_res, df_val, df_res, note=""):
        results.append({
            "source_kind": source_kind,
            "source_name": name,
            "workspace": ws_val,
            "workspace_resolution": ws_res,
            "dataflow": df_val,
            "dataflow_resolution": df_res,
            "note": note,
        })

    for source_kind, name, text, local_map, ws_tokens, df_tokens, ws_ids, df_ids in direct_hits:
        if not (ws_tokens or df_tokens or ws_ids or df_ids):
            add_result(source_kind, name, None, "n/a", None, "n/a",
                       "Uses dataflow connector but name pattern not recognized (inspect manually)")
            continue

        for token in df_tokens or [None]:
            ws_val, ws_res = (None, "n/a")
            if ws_tokens:
                ws_val, ws_res = _names_resolve_token(ws_tokens[0], local_map, global_params)
            df_val, df_res = (None, "n/a")
            if token:
                df_val, df_res = _names_resolve_token(token, local_map, global_params)
            add_result(source_kind, name, ws_val, ws_res, df_val, df_res)

        for wid in ws_ids:
            add_result(source_kind, name, f"(workspaceId GUID) {wid}", "guid", None, "n/a")
        for did in df_ids:
            add_result(source_kind, name, None, "n/a", f"(dataflowId GUID) {did}", "guid")

    # --- Pass 2: entries that don't call the connector directly, but ----
    # reference an "enumerator" query (e.g. a shared query that lists all
    # dataflows in one workspace) and then filter it by [dataflowName].
    direct_names = {name for _, name, *_ in direct_hits}
    for source_kind, name, text in entries:
        if name in direct_names:
            continue
        df_tokens = ll.field_values(text, "dataflowName")
        if not df_tokens:
            continue
        matched_enum = next((e for e in enumerators if re.search(r'\b' + re.escape(e) + r'\b', text)), None)
        if not matched_enum:
            continue
        local_map = {m.group(1): m.group(2) for m in RE_LOCAL_ASSIGN.finditer(text)}
        ws_val = enumerators[matched_enum]
        for token in df_tokens:
            df_val, df_res = _names_resolve_token(token, local_map, global_params)
            add_result(source_kind, name, ws_val, "via-enumerator:" + matched_enum, df_val, df_res)

    return results


def summarize_names(results):
    unique = defaultdict(set)
    for r in results:
        key = (r["workspace"] or "?", r["dataflow"] or "?")
        unique[key].add(f'{r["source_kind"]}:{r["source_name"]}')
    return unique


def run_names(args):
    results = extract_names(args.pbix_path)

    print(f"\n=== Dataflow references found in: {args.pbix_path} ===\n")
    unique = summarize_names(results)
    for (ws, df), users in sorted(unique.items()):
        print(f"Workspace: {ws}")
        print(f"  Dataflow: {df}")
        print(f"  Used by ({len(users)}): {sorted(users)[:5]}{' ...' if len(users) > 5 else ''}")
        print()

    print(f"Total distinct workspace/dataflow pairs: {len(unique)}")
    print(f"Total M query/table references scanned that use the dataflow connector: {len(results)}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "source_kind", "source_name", "workspace", "workspace_resolution",
                "dataflow", "dataflow_resolution", "note",
            ])
            writer.writeheader()
            writer.writerows(results)
        print(f"\nFull detail written to: {args.csv}")


# --------------------------------------------------------------------------
# "lineage" subcommand: table -> dataflow (not physical source) lineage
# --------------------------------------------------------------------------

def _lineage_resolve_token(token, local_map, global_map):
    if token.startswith('"'):
        return ll.unquote(token)
    if token in local_map:
        return local_map[token]
    if token in global_map:
        return global_map[token]
    return None


def analyze_direct_bindings(entries, global_params):
    """For each entry, determine if it directly calls the dataflow connector
    and, if so, its resolved (workspace, dataflow, entity). Also identify
    'enumerator' queries (list all dataflows of a workspace, no name filter
    yet) so indirect references can be resolved."""
    direct = {}      # name -> dict(workspace, dataflow, entity, method)
    enumerators = {}  # name -> workspace value (query lists all dataflows of a workspace)

    for _, name, text in entries:
        if not ll.RE_USES_DATAFLOW_CONNECTOR.search(text):
            continue
        local_map = {m.group(1): m.group(2) for m in RE_LOCAL_ASSIGN.finditer(text)}
        ws_tokens = ll.field_values(text, "workspaceName")
        df_tokens = ll.field_values(text, "dataflowName")
        ws_ids = [ll.unquote(t) for t in ll.field_values(text, "workspaceId")]
        df_ids = [ll.unquote(t) for t in ll.field_values(text, "dataflowId")]
        entity = ll.first_field_value(text, "entity")

        ws_val = _lineage_resolve_token(ws_tokens[0], local_map, global_params) if ws_tokens else None
        df_val = _lineage_resolve_token(df_tokens[0], local_map, global_params) if df_tokens else None
        if not ws_val and ws_ids:
            ws_val = f"(workspaceId GUID) {ws_ids[0]}"
        if not df_val and df_ids:
            df_val = f"(dataflowId GUID) {df_ids[0]}"

        if df_val:
            direct[name] = {
                "workspace": ws_val,
                "dataflow": df_val,
                "entity": entity,
                "method": "direct",
            }
        elif ws_val and not df_tokens and not df_ids:
            # lists all dataflows in a workspace -> used by other queries as
            # a base table which they then filter by [dataflowName]
            enumerators[name] = ws_val

    # Second sweep: queries that reference an enumerator + filter by [dataflowName]
    for _, name, text in entries:
        if name in direct:
            continue
        df_tokens = ll.field_values(text, "dataflowName")
        if not df_tokens:
            continue
        matched_enum = next((e for e in enumerators if re.search(r'\b' + re.escape(e) + r'\b', text)), None)
        if not matched_enum:
            continue
        local_map = {m.group(1): m.group(2) for m in RE_LOCAL_ASSIGN.finditer(text)}
        df_val = _lineage_resolve_token(df_tokens[0], local_map, global_params)
        entity = ll.first_field_value(text, "entity")
        if df_val:
            direct[name] = {
                "workspace": enumerators[matched_enum],
                "dataflow": df_val,
                "entity": entity,
                "method": f"direct (via enumerator {matched_enum})",
            }

    return direct


def build_dependency_edges(entries, all_names):
    """For each entry, find which other known table/query names it references
    (used as a data source, e.g. `Source = #"Name"` or `Source = Name`)."""
    deps = {}
    # Pre-split names into ones needing quoted-identifier form vs bare-word form
    bare_ok = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
    for _, name, text in entries:
        found = set()
        for other in all_names:
            if other == name:
                continue
            # Plain substring check (no regex escaping needed/wanted here - the
            # quoted M identifier is matched literally, so escaping characters
            # like "-" with backslashes would break the match).
            quoted_pat = '#"' + other + '"'
            if quoted_pat in text:
                found.add(other)
                continue
            if bare_ok.match(other) and re.search(r'\b' + re.escape(other) + r'\b', text):
                found.add(other)
        deps[name] = found
    return deps


def resolve_lineage(name, direct, deps, cache, visiting):
    if name in cache:
        return cache[name]
    if name in direct:
        result = {**direct[name], "path": []}
        cache[name] = result
        return result
    if name in visiting:
        return None  # cycle guard
    visiting.add(name)
    best = None
    for dep in sorted(deps.get(name, [])):
        sub = resolve_lineage(dep, direct, deps, cache, visiting)
        if sub:
            best = {**sub, "path": [dep] + sub["path"], "method": "derived (" + sub["method"] + ")"}
            break
    visiting.discard(name)
    cache[name] = best
    return best


def run_lineage(args):
    entries = load_entries(args.pbix_path)
    global_params = build_global_param_values([(n, e) for _, n, e in entries])
    direct = analyze_direct_bindings(entries, global_params)
    all_names = [n for _, n, _ in entries]
    deps = build_dependency_edges(entries, all_names)

    cache = {}
    rows = []
    for source_kind, name, _ in entries:
        if args.table and name != args.table:
            continue
        result = resolve_lineage(name, direct, deps, cache, set())
        if result:
            rows.append({
                "pbi_table_name": name,
                "source_kind": source_kind,
                "dataflow_workspace": result.get("workspace") or "",
                "dataflow_name": result.get("dataflow") or "",
                "dataflow_entity": result.get("entity") or "",
                "resolution_method": result.get("method"),
                "derivation_path": " -> ".join(result.get("path", [])),
                "note": "",
            })
        else:
            rows.append({
                "pbi_table_name": name,
                "source_kind": source_kind,
                "dataflow_workspace": "",
                "dataflow_name": "",
                "dataflow_entity": "",
                "resolution_method": "unresolved",
                "derivation_path": "",
                "note": "No dataflow-connected ancestor found (likely built from static/inline data or another non-dataflow source)",
            })

    # --- console summary ---
    resolved = [r for r in rows if r["resolution_method"] != "unresolved"]
    unresolved = [r for r in rows if r["resolution_method"] == "unresolved"]
    print(f"\n=== Table -> Dataflow lineage for: {args.pbix_path} ===\n")
    for r in resolved:
        chain = f"  (via {r['derivation_path']})" if r["derivation_path"] else ""
        print(f"{r['pbi_table_name']:45s} | entity={r['dataflow_entity'] or '?':30s} | "
              f"dataflow={r['dataflow_name']:40s} | workspace={r['dataflow_workspace']}{chain}")

    print(f"\nResolved: {len(resolved)}   Unresolved: {len(unresolved)}   Total scanned: {len(rows)}")
    if unresolved:
        print("\nUnresolved entries (no dataflow ancestor found):")
        for r in unresolved:
            print(f"  - [{r['source_kind']}] {r['pbi_table_name']}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "pbi_table_name", "source_kind", "dataflow_workspace", "dataflow_name",
                "dataflow_entity", "resolution_method", "derivation_path", "note",
            ])
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nFull detail written to: {args.csv}")


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    names_ap = sub.add_parser("names", help="List workspace/dataflow name pairs referenced in a PBIX")
    names_ap.add_argument("pbix_path", help="Path to the .pbix file")
    names_ap.add_argument("--csv", help="Optional path to write full results as CSV")
    names_ap.set_defaults(func=run_names)

    lineage_ap = sub.add_parser("lineage", help="Trace each table/query to its source dataflow + entity")
    lineage_ap.add_argument("pbix_path", help="Path to the .pbix file")
    lineage_ap.add_argument("--csv", help="Optional path to write results as CSV")
    lineage_ap.add_argument("--table", help="Only show lineage for a single table/query name")
    lineage_ap.set_defaults(func=run_lineage)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
