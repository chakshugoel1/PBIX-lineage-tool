#!/usr/bin/env python
"""
extract_dataflow_names.py

Extracts all Power BI / Power Platform Dataflow references (workspace + dataflow
name pairs) used inside a .pbix file's Power Query (M) source code.

Handles:
  - Literal (hard-coded) workspace/dataflow names, e.g.
        Table.SelectRows(Workspaces, each [workspaceName] = "WKS DTF FINANCE")
  - Parametrised references, where the workspace/dataflow name is supplied via
    an M parameter or a local "let" variable, e.g.
        Table.SelectRows(Workspaces, each [workspaceName] = WKS_PARAM_DIM)
    -> resolved using the parameter's current stored value.
  - GUID-bound references, e.g.
        Workspaces{[workspaceId="..."]}[Data]
        ...{[dataflowId="..."]}[Data]

Requires: pip install pbixray

Usage:
    python extract_dataflow_names.py "path\to\report.pbix"
    python extract_dataflow_names.py "path\to\report.pbix" --csv out.csv
"""

import argparse
import re
import sys
from collections import defaultdict

try:
    from pbixray import PBIXRay
except ImportError:
    sys.exit("Missing dependency. Install with:  pip install pbixray")

# --- regex patterns -----------------------------------------------------
RE_WORKSPACE_NAME = re.compile(r'\[workspaceName\]\s*=\s*("(?:[^"\\]|\\.)*"|[A-Za-z_][A-Za-z0-9_\-]*)')
RE_DATAFLOW_NAME = re.compile(r'\[dataflowName\]\s*=\s*("(?:[^"\\]|\\.)*"|[A-Za-z_][A-Za-z0-9_\-]*)')
RE_WORKSPACE_ID = re.compile(r'\[workspaceId\s*=\s*"([^"]+)"\]')
RE_DATAFLOW_ID = re.compile(r'\[dataflowId\s*=\s*"([^"]+)"\]')
RE_USES_DATAFLOW_CONNECTOR = re.compile(r'PowerPlatform\.Dataflows|Dataflows\.Contents')
RE_LOCAL_ASSIGN = re.compile(r'\b([A-Za-z_][A-Za-z0-9_\-]*)\s*=\s*"((?:[^"\\]|\\.)*)"')
RE_SIMPLE_QUOTED = re.compile(r'^"((?:[^"\\]|\\.)*)"')


def unquote(token: str) -> str:
    """Strip surrounding quotes from a literal M string token, if present."""
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1].replace('\\"', '"')
    return token


def build_global_param_values(rows):
    """Map parameter/query name -> its resolved literal string value (if any)."""
    values = {}
    for name, expr in rows:
        if expr is None:
            continue
        text = str(expr)
        m = RE_SIMPLE_QUOTED.match(text.strip())
        if m:
            values[name] = m.group(1)
    return values


def resolve_token(token: str, local_map: dict, global_map: dict):
    """Resolve a captured token to a literal string, following local then global scope."""
    if token.startswith('"'):
        return unquote(token), "literal"
    if token in local_map:
        return local_map[token], "local-variable"
    if token in global_map:
        return global_map[token], "parameter"
    return None, "unresolved"


def extract(pbix_path: str):
    model = PBIXRay(pbix_path)

    # Combine table-partition M code + shared queries/parameters into one list.
    entries = []  # (source_kind, name, expression)
    for _, row in model.power_query.iterrows():
        entries.append(("table", row["TableName"], row["Expression"]))
    for _, row in model.m_parameters.iterrows():
        entries.append(("query", row["ParameterName"], row["Expression"]))

    global_params = build_global_param_values([(n, e) for _, n, e in entries])

    # --- Pass 1: entries that call the dataflow connector directly ------
    direct_hits = []  # (source_kind, name, text, local_map, ws_tokens, df_tokens, ws_ids, df_ids)
    enumerators = {}  # query_name -> resolved workspace value (queries that list all
                       # dataflows of one workspace, with no dataflow-name filter yet)
    for source_kind, name, expr in entries:
        if expr is None:
            continue
        text = str(expr)
        if not RE_USES_DATAFLOW_CONNECTOR.search(text):
            continue

        local_map = {m.group(1): m.group(2) for m in RE_LOCAL_ASSIGN.finditer(text)}
        ws_tokens = RE_WORKSPACE_NAME.findall(text)
        df_tokens = RE_DATAFLOW_NAME.findall(text)
        ws_ids = RE_WORKSPACE_ID.findall(text)
        df_ids = RE_DATAFLOW_ID.findall(text)
        direct_hits.append((source_kind, name, text, local_map, ws_tokens, df_tokens, ws_ids, df_ids))

        if ws_tokens and not df_tokens and not ws_ids and not df_ids:
            ws_val, _ = resolve_token(ws_tokens[0], local_map, global_params)
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
                ws_val, ws_res = resolve_token(ws_tokens[0], local_map, global_params)
            df_val, df_res = (None, "n/a")
            if token:
                df_val, df_res = resolve_token(token, local_map, global_params)
            add_result(source_kind, name, ws_val, ws_res, df_val, df_res)

        for wid in ws_ids:
            add_result(source_kind, name, f"(workspaceId GUID) {wid}", "guid", None, "n/a")
        for did in df_ids:
            add_result(source_kind, name, None, "n/a", f"(dataflowId GUID) {did}", "guid")

    # --- Pass 2: entries that don't call the connector directly, but ----
    # reference an "enumerator" query (e.g. a shared query that lists all
    # dataflows in one workspace) and then filter it by [dataflowName].
    direct_names = {name for _, name, *_ in direct_hits}
    for source_kind, name, expr in entries:
        if expr is None or name in direct_names:
            continue
        text = str(expr)
        df_tokens = RE_DATAFLOW_NAME.findall(text)
        if not df_tokens:
            continue
        matched_enum = next((e for e in enumerators if re.search(r'\b' + re.escape(e) + r'\b', text)), None)
        if not matched_enum:
            continue
        local_map = {m.group(1): m.group(2) for m in RE_LOCAL_ASSIGN.finditer(text)}
        ws_val = enumerators[matched_enum]
        for token in df_tokens:
            df_val, df_res = resolve_token(token, local_map, global_params)
            add_result(source_kind, name, ws_val, "via-enumerator:" + matched_enum, df_val, df_res)

    return results


def summarize(results):
    unique = defaultdict(set)
    for r in results:
        key = (r["workspace"] or "?", r["dataflow"] or "?")
        unique[key].add(f'{r["source_kind"]}:{r["source_name"]}')
    return unique


def main():
    ap = argparse.ArgumentParser(description="Extract Dataflow names referenced inside a PBIX file.")
    ap.add_argument("pbix_path", help="Path to the .pbix file")
    ap.add_argument("--csv", help="Optional path to write full results as CSV")
    args = ap.parse_args()

    results = extract(args.pbix_path)

    print(f"\n=== Dataflow references found in: {args.pbix_path} ===\n")
    unique = summarize(results)
    for (ws, df), users in sorted(unique.items()):
        print(f"Workspace: {ws}")
        print(f"  Dataflow: {df}")
        print(f"  Used by ({len(users)}): {sorted(users)[:5]}{' ...' if len(users) > 5 else ''}")
        print()

    print(f"Total distinct workspace/dataflow pairs: {len(unique)}")
    print(f"Total M query/table references scanned that use the dataflow connector: {len(results)}")

    if args.csv:
        import csv
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "source_kind", "source_name", "workspace", "workspace_resolution",
                "dataflow", "dataflow_resolution", "note",
            ])
            writer.writeheader()
            writer.writerows(results)
        print(f"\nFull detail written to: {args.csv}")


if __name__ == "__main__":
    main()
