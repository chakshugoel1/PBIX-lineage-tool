#!/usr/bin/env python
"""
extract_table_dataflow_lineage.py

Produces a Table -> Dataflow lineage report for a .pbix file: for every table
(and shared query) in the model, it identifies which Dataflow entity/table it
was ultimately loaded from, even when the table doesn't call the dataflow
connector directly but is derived from another local query/table that does.

Output columns:
  pbi_table_name      - the Power BI model table / query name
  source_kind          - "table" (model table) or "query" (shared/parameter query)
  dataflow_workspace   - resolved workspace name
  dataflow_name        - resolved dataflow name
  dataflow_entity      - the entity/table name *inside* the dataflow that was
                         selected (e.g. "101_DIRECTION"); blank if not found
  resolution_method    - how the binding was determined:
                           direct            = this table calls the dataflow
                                                connector itself
                           derived           = traced through 1+ local
                                                queries/tables to find the
                                                nearest dataflow-bound ancestor
                           unresolved        = no dataflow connector reachable
                                                (e.g. built from inline/static
                                                data, or a dead-end reference)
  derivation_path      - chain of table/query names walked to reach the
                         resolved dataflow-bound ancestor (empty if direct)
  note                 - extra detail / caveats

Requires: pip install pbixray

Usage:
    python extract_table_dataflow_lineage.py "path\to\report.pbix"
    python extract_table_dataflow_lineage.py "path\to\report.pbix" --csv lineage.csv
    python extract_table_dataflow_lineage.py "path\to\report.pbix" --table "101_DIRECTION"
"""

import argparse
import csv
import re
import sys

try:
    from pbixray import PBIXRay
except ImportError:
    sys.exit("Missing dependency. Install with:  pip install pbixray")

import lineage_lib as ll

# --- regex patterns -------------------------------------------------------
# workspaceName/dataflowName/workspaceId/dataflowId/entity extraction and the
# connector-call detector live in lineage_lib.py (ll.field_values /
# ll.first_field_value / ll.RE_USES_DATAFLOW_CONNECTOR) so this standalone
# script can't drift out of sync with the main pipeline's parsing logic, as
# it previously did.
RE_LOCAL_ASSIGN = re.compile(r'\b([A-Za-z_][A-Za-z0-9_\-]*)\s*=\s*"((?:[^"\\]|\\.)*)"')
RE_SIMPLE_QUOTED = re.compile(r'^"((?:[^"\\]|\\.)*)"')


def unquote(token: str) -> str:
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1].replace('\\"', '"')
    return token


def resolve_token(token, local_map, global_map):
    if token.startswith('"'):
        return unquote(token)
    if token in local_map:
        return local_map[token]
    if token in global_map:
        return global_map[token]
    return None


def build_global_param_values(entries):
    values = {}
    for name, expr in entries:
        if expr is None:
            continue
        m = RE_SIMPLE_QUOTED.match(str(expr).strip())
        if m:
            values[name] = m.group(1)
    return values


def extract_entity(text: str):
    return ll.first_field_value(text, "entity")


def load_entries(pbix_path):
    model = PBIXRay(pbix_path)
    entries = []  # (source_kind, name, expression_text)
    for _, row in model.power_query.iterrows():
        entries.append(("table", row["TableName"], str(row["Expression"])))
    for _, row in model.m_parameters.iterrows():
        entries.append(("query", row["ParameterName"], str(row["Expression"])))
    return entries


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
        entity = extract_entity(text)

        ws_val = resolve_token(ws_tokens[0], local_map, global_params) if ws_tokens else None
        df_val = resolve_token(df_tokens[0], local_map, global_params) if df_tokens else None
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
        df_val = resolve_token(df_tokens[0], local_map, global_params)
        entity = extract_entity(text)
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


def main():
    ap = argparse.ArgumentParser(description="Extract Table -> Dataflow lineage from a PBIX file.")
    ap.add_argument("pbix_path", help="Path to the .pbix file")
    ap.add_argument("--csv", help="Optional path to write results as CSV")
    ap.add_argument("--table", help="Only show lineage for a single table/query name")
    args = ap.parse_args()

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


if __name__ == "__main__":
    main()
