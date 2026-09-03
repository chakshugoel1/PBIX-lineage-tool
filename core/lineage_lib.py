"""
lineage_lib.py

Core parsing/resolution utilities for the PBIX -> Dataflow(s) -> Physical
Source lineage tool.

Two "universes" of M code are handled uniformly:
  - the PBIX report itself (power_query tables + m_parameters / shared queries)
  - each dataflow's own pbi:mashup.document (split into its shared queries)

Resolution has two stages:
  1. PBIX-level: for a report table, walk local query dependencies to find
     the nearest ancestor that calls the dataflow connector
     (PowerPlatform.Dataflows / Dataflows.Contents), directly or via an
     "enumerator" query filtered by [dataflowName]. This yields
     (workspace, dataflow_name, entity_name) = "Level 1".
  2. Dataflow-level: open the Level-1 dataflow's JSON. If the entity is a
     LocalEntity/CalculatedEntity, resolve its own M query (possibly walking
     local dependencies within that dataflow) down to a physical connector
     (Oracle.Database, Excel.Workbook, SharePoint.Files, Csv.Document,
     Json.Document, Web.Contents). If the entity is a ReferenceEntity (a
     linked/indirected entity), search all other provided dataflow JSON
     files for one that locally defines an entity with the same name -
     that's "Level 2" - and recurse.
"""

import glob
import json
import logging
import os
import re
import time
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def powerbi_service_url(workspace_id, dataflow_id):
    """Build the PowerBI service URL for a workspace/dataflow GUID pair, in
    the same form used to look up a dataflow's display name when the M code
    or ReferenceEntity JSON only carries GUIDs (no literal names):
    https://app.powerbi.com/groups/{workspaceId}/dataflows/{dataflowId}
    Note: this URL requires an authenticated PowerBI session to view; it
    cannot be fetched headlessly. See load_guid_cache() / GUID_CACHE_PATH."""
    return f"https://app.powerbi.com/groups/{workspace_id}/dataflows/{dataflow_id}"


GUID_CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "guid_dataflow_names.json")


def load_guid_cache(path=None):
    """Load GUID lookup entries used to resolve dataflow references.

    Legacy entries use ``{"workspaceId/dataflowId": "Dataflow Name"}``.
    Rich entries may additionally provide ``workspace_name`` and
    ``dataflow_name``. Both formats remain supported.
    resolve GUID-only dataflow references (ReferenceEntity `modelId`, or
    M-code `[workspaceId]`/`[dataflowId]` filters with no literal name
    alongside them) into a friendly name, since app.powerbi.com requires an
    authenticated session and can't be resolved by an automated fetch.
    Entries with a null/empty value are treated as "not yet known" and
    skipped. Missing file -> empty cache (feature simply does nothing)."""
    path = path or GUID_CACHE_PATH
    if not os.path.exists(path):
        return {}
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}
    return {k: v for k, v in raw.items() if v}


def guid_cache_dataflow_name(guid_cache, key):
    """Return the friendly dataflow name from either supported cache shape."""
    value = (guid_cache or {}).get(key)
    if isinstance(value, dict):
        return value.get("dataflow_name") or value.get("name")
    return value


def guid_cache_workspace_name(guid_cache, key):
    """Return an optional workspace display name from a rich cache entry."""
    value = (guid_cache or {}).get(key)
    return value.get("workspace_name") if isinstance(value, dict) else None

def classify_unresolved_reason(reason):
    """Map a free-text 'unresolved'/failure reason string to a short,
    consistent issue-type tag, used to make every NEEDS MANUAL REVIEW flag
    say *why* in a scannable, consistent way (instead of just "unresolved")."""
    r = (reason or "").lower()
    if "not found among the provided files" in r or "dataflow file for" in r:
        return "DATAFLOW NOT FOUND"
    if "no dataflow-connector ancestor" in r:
        return "NO CONNECTOR ANCESTOR"
    if "linked/reference entity" in r or "modelid" in r or "guid" in r:
        return "UNRESOLVABLE GUID / REFERENCE ENTITY"
    if "cycle" in r:
        return "CYCLE DETECTED"
    if "no recognizable source connector" in r:
        return "NO RECOGNIZABLE CONNECTOR"
    if "no m query named" in r:
        return "QUERY NOT FOUND IN DATAFLOW"
    return "UNRESOLVED - OTHER"


# --------------------------------------------------------------------------
# Shared regex patterns (workspace/dataflow/entity resolution) - adapted
# from legacy_cli.py's original standalone scripts
# --------------------------------------------------------------------------
RE_USES_DATAFLOW_CONNECTOR = re.compile(r'PowerPlatform\.Dataflows|Dataflows\.Contents|PowerBI\.Dataflows')

# Power Query renders a "field = value" record filter/selector in one of two
# shapes depending on how the M code was authored, and both are common:
#   Style A: [field] = value     (a filter predicate, e.g. `each [x] = y`)
#   Style B: [field = value, field2 = value2, ...]   (a record-selector key
#            lookup, e.g. T{[x = y]} or Oracle's T{[Schema = "S", Item = "I"]},
#            what Power BI's Navigator UI auto-generates - M records can hold
#            any number of comma-separated field=value pairs in one bracket)
# RE_FIELD_EQ_A / RE_RECORD_SELECTOR+RE_FIELD_PAIR match either shape for ANY
# field name in one pass, so looking for a field this tool doesn't already
# know about needs no new regex - just a new field_values()/
# first_field_value() call below.
_FIELD_IDENT = r'[A-Za-z_][A-Za-z0-9_]*'
_FIELD_VALUE = r'"(?:[^"\\]|\\.)*"|#"(?:[^"\\]|\\.)*"|[A-Za-z_][A-Za-z0-9_.\-]*'
_FIELD_PAIR = rf'{_FIELD_IDENT}\s*=\s*(?:{_FIELD_VALUE})'
RE_FIELD_EQ_A = re.compile(rf'\[\s*({_FIELD_IDENT})\s*\]\s*=\s*({_FIELD_VALUE})')       # Style A
RE_RECORD_SELECTOR = re.compile(rf'\[\s*({_FIELD_PAIR}(?:\s*,\s*{_FIELD_PAIR})*)\s*\]')  # Style B (whole bracket body)
RE_FIELD_PAIR = re.compile(rf'({_FIELD_IDENT})\s*=\s*({_FIELD_VALUE})')                 # one field=value pair within a Style B body
RE_LOCAL_ASSIGN = re.compile(r'\b([A-Za-z_][A-Za-z0-9_\-]*)\s*=\s*"((?:[^"\\]|\\.)*)"')
RE_LOCAL_ASSIGN_QUOTED = re.compile(r'#"([^"]+)"\s*=\s*"((?:[^"\\]|\\.)*)"')
RE_SIMPLE_QUOTED = re.compile(r'^"((?:[^"\\]|\\.)*)"')


def strip_m_comments(text):
    """Remove M `//line` and `/* block */` comments from `text` before any
    connector/field regex runs against it, so a commented-out (no longer
    active) dataflow/connector call can't be mistaken for the query's real
    source - e.g. an old `//Source = PowerBI.Dataflows(null), ...` line left
    behind after a query was repointed at a local reference. Respects M
    string literals (a doubled quote is M's escape for a literal quote
    inside a string) so a `//` inside a URL string like "http://..." is
    never treated as a comment start. Block comments are collapsed to
    whitespace (newlines preserved) rather than dropped outright, so
    line-based regexes/offsets downstream aren't affected and adjacent
    tokens don't get merged."""
    if not text or not isinstance(text, str):
        return text
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n:
                if text[j] == '"':
                    if j + 1 < n and text[j + 1] == '"':
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(text[i:j])
            i = j
        elif text[i:i + 2] == '//':
            j = text.find('\n', i)
            if j == -1:
                i = n
            else:
                out.append('\n')
                i = j + 1
        elif text[i:i + 2] == '/*':
            j = text.find('*/', i + 2)
            end = j + 2 if j != -1 else n
            inner = text[i:end]
            out.append('\n' * inner.count('\n') if '\n' in inner else ' ')
            i = end
        else:
            out.append(ch)
            i += 1
    return ''.join(out)


def extract_fields(text):
    """Scan `text` for every '[field] = value' (Style A) occurrence and every
    '[field = value, field2 = value2, ...]' record-selector body (Style B,
    one or more comma-separated field=value pairs in a single bracket - the
    general M record-literal/selector shape, e.g. `{[Schema = "S", Item =
    "I"]}`) and return {field_name: [raw_value_tokens...]} for every field
    name found - not just ones this tool already looks for. Raw tokens are
    returned as-matched (quotes included if quoted); callers that need a
    plain string should unquote() the token themselves (see workspace/
    dataflow/entity usage below), same as before this was generalized."""
    if not text or not isinstance(text, str):
        return {}
    found = {}
    try:
        for m in RE_FIELD_EQ_A.finditer(text):
            found.setdefault(m.group(1), []).append(m.group(2))
        for rm in RE_RECORD_SELECTOR.finditer(text):
            for m in RE_FIELD_PAIR.finditer(rm.group(1)):
                found.setdefault(m.group(1), []).append(m.group(2))
    except Exception as e:
        logger.error(f"Error extracting fields from M code: {e}")
    return found


def field_values(text, field_name):
    return extract_fields(text).get(field_name, [])


def first_field_value(text, field_name):
    """First occurrence of `field_name`, unquoted to a plain string (or None)."""
    vals = field_values(text, field_name)
    return unquote(vals[0]) if vals else None


def resolve_value_token(token, local_map, universe, global_params=None):
    """Resolve a raw field-value token (as returned by field_values /
    extract_fields) to its literal string value: unquote directly if it's a
    quoted literal, else the token is a bare reference (a local `let`-step
    variable, a global M parameter, or a separately-named query) - look it
    up instead of returning the reference name itself. Used for every field
    (workspaceName/dataflowName/entity/...), since M lets any of them be
    supplied via a local variable, e.g. `{[entity = Table_Source]}` where
    `Table_Source = "30217-1_DEMAT_MODEL"` is a step earlier in the same
    query - the raw token there is the bare word "Table_Source", not the
    table name, and must be resolved the same way workspace/dataflow tokens
    already were, or it ends up used verbatim as a bogus entity/dataflow
    name."""
    if token is None:
        return None
    if not isinstance(token, str):
        return None
    token = token.strip()
    if token.startswith('"'):
        return unquote(token)
    if token in local_map:
        return local_map[token]
    if global_params and token in global_params:
        return global_params[token]
    if universe:
        return universe.resolve_literal(token)
    return None


def _local_map_of(text):
    local_map = {m.group(1): m.group(2) for m in RE_LOCAL_ASSIGN.finditer(text)}
    local_map.update({m.group(1): m.group(2) for m in RE_LOCAL_ASSIGN_QUOTED.finditer(text)})
    return local_map

# physical-connector patterns (with improved whitespace handling)
RE_ORACLE = re.compile(r'Oracle\.Database\s*\(\s*(?://[^\n]*\n\s*)*(#"[^"]+"|"[^"]*"|[A-Za-z_][\w]*)', re.MULTILINE)

RE_SHAREPOINT_SITE = re.compile(r'SharePoint\.Files\s*\(\s*(?://[^\n]*\n\s*)*(#"[^"]+"|"[^"]*")', re.MULTILINE)
RE_FOLDER_PATH_FILTER = re.compile(r'\[Folder Path\]\s*=\s*(#"[^"]+"|"[^"]*")')
RE_TEXT_CONTAINS_FOLDER = re.compile(r'Text\.Contains\(\[Folder Path\],\s*"([^"]+)"\)')
RE_NAME_FILTER = re.compile(r'\[Name\]\s*=\s*"([^"]+)"')
RE_EXCEL_WORKBOOK = re.compile(r'Excel\.Workbook\s*\(')
RE_CSV_DOCUMENT = re.compile(r'Csv\.Document\s*\(\s*(?://[^\n]*\n\s*)*(#"[^"]+"|"[^"]*"|[A-Za-z_][\w]*)', re.MULTILINE)
RE_JSON_DOCUMENT = re.compile(r'Json\.Document\s*\(')
RE_WEB_CONTENTS = re.compile(r'Web\.Contents\s*\(\s*"([^"]+)"')
RE_WEB_RELATIVE_PATH = re.compile(r'RelativePath\s*=\s*"([^"]+)"')

RE_TABLE_COMBINE = re.compile(r'Table\.Combine\(\s*\{(.*?)\}\s*\)', re.DOTALL)
RE_SOURCE_STEP = re.compile(r'\bSource\s*=\s*(#"[^"]+"|[A-Za-z_][\w]*)\s*[,\n]')
RE_LET_KEYWORD = re.compile(r'\blet\b')
RE_FIRST_STEP_NAME = re.compile(r'\s*(#"(?:[^"\\]|\\.)*"|[A-Za-z_][\w]*)\s*=\s*')

CONNECTOR_ORDER = [
    ("Oracle Database", re.compile(r'Oracle\.Database\(')),
    ("SharePoint Excel/CSV", re.compile(r'SharePoint\.Files\(')),
    ("Excel Workbook", re.compile(r'Excel\.Workbook\(')),
    ("Csv Document", re.compile(r'Csv\.Document\(')),
    ("Web Contents", re.compile(r'Web\.Contents\(')),
    # Parsers are only sources of last resort; prefer the transport they wrap.
    ("Json Document", re.compile(r'Json\.Document\(')),
]


def unquote(token):
    """Unquote an M string literal, returning None if token is None or not a valid string."""
    if token is None:
        return None
    if not isinstance(token, str):
        logger.warning(f"unquote() called with non-string type {type(token).__name__}: {token}")
        return None
    token = token.strip()
    if not token:
        return None
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1].replace('\\"', '"')
    return token


def strip_quoted_ident(token):
    """Strip an M #"..." quoted-identifier wrapper, if present."""
    token = token.strip()
    if token.startswith('#"') and token.endswith('"'):
        return token[2:-1]
    return token


def find_names_referenced(text, candidate_names):
    """Return the subset of candidate_names that text appears to reference
    (either as #"Name" quoted identifier or bare word)."""
    found = set()
    bare_ok = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
    for other in candidate_names:
        quoted_pat = '#"' + other + '"'
        if quoted_pat in text:
            found.add(other)
            continue
        if bare_ok.match(other) and re.search(r'\b' + re.escape(other) + r'\b', text):
            found.add(other)
    return found


def extract_table_combine_members(text):
    """If text contains Table.Combine({...}), return the list of member
    names referenced inside the braces (order preserved), else None."""
    m = RE_TABLE_COMBINE.search(text)
    if not m:
        return None
    inner = m.group(1)
    members = []
    for tok in re.finditer(r'#"((?:[^"\\]|\\.)*)"|([A-Za-z_][\w]*)', inner):
        name = tok.group(1) if tok.group(1) is not None else tok.group(2)
        if name:
            members.append(name)
    return members


def first_step_rhs(text):
    """Return the RHS expression text of the FIRST `let` step, using a
    bracket/quote-depth-aware scan (a plain regex can't reliably find the
    end of the first step because RHS expressions often contain nested
    commas inside parens/brackets/braces)."""
    m = RE_LET_KEYWORD.search(text)
    if not m:
        return None
    body = text[m.end():]
    m2 = RE_FIRST_STEP_NAME.match(body)
    if not m2:
        return None
    i = m2.end()
    n = len(body)
    depth = 0
    in_str = False
    start = i
    while i < n:
        ch = body[i]
        if in_str:
            if ch == '"' and body[i - 1] != '\\':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in '([{':
                depth += 1
            elif ch in ')]}':
                depth -= 1
            elif ch == ',' and depth == 0:
                break
        i += 1
    return body[start:i]


class Universe:
    """A set of named M expressions (either a PBIX's tables/parameters, or
    one dataflow's shared queries) with dependency-graph resolution."""

    def __init__(self, entries):
        # entries: dict name -> expression text. Comments are stripped once
        # here so every consumer (field/connector regexes, dependency
        # scanning, etc.) automatically sees only the query's active code.
        self.texts = {name: strip_m_comments(text) for name, text in entries.items()}
        self.names = list(self.texts.keys())
        self._deps_cache = {}

    def get(self, name):
        """Get expression text by name, logging warnings for empty/None values."""
        value = self.texts.get(name)
        if value == "":
            logger.warning(f"Query/table '{name}' has empty M expression (may indicate corrupted PBIX export)")
        elif value is None:
            logger.debug(f"Query/table '{name}' not found in universe")
        return value

    def __contains__(self, name):
        return name in self.texts

    def deps_of(self, name):
        if name in self._deps_cache:
            return self._deps_cache[name]
        text = self.texts.get(name, "")
        others = [n for n in self.names if n != name]
        found = find_names_referenced(text, others)
        self._deps_cache[name] = found
        return found

    def ordered_deps(self, name):
        """Dependencies of `name`, with the dependency referenced in the
        FIRST `let` step (i.e. the true source-of-data step) listed first,
        ahead of dependencies that are only used incidentally later (e.g.
        as a filter bound/parameter). This avoids following a coincidental
        bare-word match down the wrong lineage path."""
        all_deps = self.deps_of(name)
        if not all_deps:
            return []
        text = self.texts.get(name, "")
        rhs = first_step_rhs(text)
        primary = find_names_referenced(rhs, all_deps) if rhs else set()
        rest = sorted(all_deps - primary)
        return sorted(primary) + rest

    def resolve_literal(self, token):
        """Resolve a token (quoted literal, #"Name", or bare Name) to a
        literal string value, following the referenced query's own
        expression if it is itself a simple quoted literal (optionally
        followed by ` meta [...]`)."""
        if token is None:
            return None
        token = token.strip()
        if token.startswith('"'):
            return unquote(token)
        name = strip_quoted_ident(token)
        text = self.texts.get(name)
        if text is None:
            return None
        m = RE_SIMPLE_QUOTED.match(text.strip())
        if m:
            return m.group(1)
        return None

    def build_global_param_values(self):
        values = {}
        for name in self.names:
            text = self.texts.get(name)
            if text is None:
                continue
            m = RE_SIMPLE_QUOTED.match(str(text).strip())
            if m:
                values[name] = m.group(1)
        return values


def analyze_direct_dataflow_bindings(universe: Universe, global_params: dict, guid_cache=None):
    """Find entries in `universe` that directly call the dataflow connector,
    plus 'enumerator' queries (list all dataflows of one workspace with no
    dataflow-name filter). Returns (direct, enumerators, unrecognized), where
    `unrecognized` is a list of {"query": name, "snippet": ...} entries for
    queries that call the dataflow connector but whose workspace/dataflow
    field syntax RE_FIELD_EQ couldn't recognize at all - a signal that a new,
    not-yet-supported M shape is in use and this tool's regex needs updating,
    instead of that query silently being treated as if it had no connector."""
    guid_cache = guid_cache or {}
    direct = {}
    enumerators = {}
    unrecognized = []

    for name in universe.names:
        text = universe.get(name) or ""
        if not RE_USES_DATAFLOW_CONNECTOR.search(text):
            continue
        fields = extract_fields(text)
        ws_tokens = fields.get("workspaceName", [])
        df_tokens = fields.get("dataflowName", [])
        ws_ids = [unquote(t) for t in fields.get("workspaceId", [])]
        df_ids = [unquote(t) for t in fields.get("dataflowId", [])]
        if not (ws_tokens or df_tokens or ws_ids or df_ids):
            unrecognized.append({"query": name, "snippet": text.strip()[:300]})
            continue
        local_map = _local_map_of(text)
        entity_raw = fields.get("entity", [None])[0]
        entity = resolve_value_token(entity_raw, local_map, universe, global_params)

        ws_val = resolve_value_token(ws_tokens[0], local_map, universe, global_params) if ws_tokens else None
        df_val = resolve_value_token(df_tokens[0], local_map, universe, global_params) if df_tokens else None
        cache_key = f"{ws_ids[0]}/{df_ids[0]}" if ws_ids and df_ids else None
        if not df_val and cache_key:
            df_val = guid_cache_dataflow_name(guid_cache, cache_key)
        if not ws_val and cache_key:
            ws_val = guid_cache_workspace_name(guid_cache, cache_key)
        if not ws_val and ws_ids:
            ws_val = f"(workspaceId GUID) {ws_ids[0]}"
        if not df_val and df_ids:
            df_val = f"(dataflowId GUID) {df_ids[0]}"

        if df_val:
            direct[name] = {
                "workspace": ws_val, "workspace_id": ws_ids[0] if ws_ids else None,
                "dataflow": df_val, "dataflow_id": df_ids[0] if df_ids else None,
                "entity": entity, "method": "direct",
            }
        elif ws_val and not df_tokens and not df_ids:
            enumerators[name] = ws_val

    # second sweep: queries that reference an enumerator + filter by [dataflowName]
    for name in universe.names:
        if name in direct:
            continue
        text = universe.get(name) or ""
        fields = extract_fields(text)
        df_tokens = fields.get("dataflowName", [])
        if not df_tokens:
            continue
        matched_enum = next((e for e in enumerators if re.search(r'\b' + re.escape(e) + r'\b', text)), None)
        if not matched_enum:
            continue
        local_map = _local_map_of(text)

        df_val = resolve_value_token(df_tokens[0], local_map, universe, global_params)
        entity_raw = fields.get("entity", [None])[0]
        entity = resolve_value_token(entity_raw, local_map, universe, global_params)
        if df_val:
            direct[name] = {
                "workspace": enumerators[matched_enum], "workspace_id": None,
                "dataflow": df_val,
                "dataflow_id": None,
                "entity": entity,
                "method": f"direct (via enumerator {matched_enum})",
            }
    return direct, enumerators, unrecognized


def build_entity_of(universe: Universe):
    """For every name in the universe, independently extract an [entity=...]
    filter/index found in ITS OWN text, regardless of whether that text
    directly calls the dataflow connector. This is needed because the
    common 2-step pattern is:
        DTF_DATA_TABLES_FACT = PowerPlatform.Dataflows(...) -> filtered to
            one dataflow's [Data] (no entity chosen yet - this is `direct`)
        X_TEMP = let Data1 = DTF_DATA_TABLES_FACT,
                     X1 = Data1{[entity="X"]}[Data] in X1   (chooses entity)
    so the entity filter usually lives one hop away from the `direct` node."""
    entity_of = {}
    for name in universe.names:
        text = universe.get(name) or ""
        entity_raw = field_values(text, "entity")
        entity_of[name] = resolve_value_token(entity_raw[0], _local_map_of(text), universe) if entity_raw else None
    return entity_of


def resolve_pbix_lineage(name, universe, direct, entity_of, cache, visiting):
    """Walk local dependency chain to nearest dataflow-bound ancestor.
    Returns dict(workspace, dataflow, entity, method, path=[hop names]) or
    None if unresolved."""
    if name in cache:
        return cache[name]
    my_entity = entity_of.get(name)
    if name in direct:
        result = {**direct[name], "entity": direct[name].get("entity") or my_entity, "path": []}
        cache[name] = result
        return result
    if name in visiting:
        return None
    visiting.add(name)
    best = None
    for dep in universe.ordered_deps(name):
        sub = resolve_pbix_lineage(dep, universe, direct, entity_of, cache, visiting)
        if sub:
            entity = my_entity or sub["entity"]
            best = {**sub, "entity": entity, "path": [dep] + sub["path"], "method": sub["method"]}
            break
    visiting.discard(name)
    cache[name] = best
    return best


def chain_hits_unrecognized(name, universe, unrecognized_names, visiting=None):
    """True if `name`'s local M dependency chain passes through a query
    flagged in `unrecognized_names` (the "unrecognized" list returned by
    analyze_direct_dataflow_bindings). Lets a report distinguish "this table
    genuinely doesn't use a dataflow" from "this table's dataflow-connector
    query uses an M syntax this tool doesn't recognize yet" - both otherwise
    look identical (an unresolved resolve_pbix_lineage walk)."""
    if not unrecognized_names:
        return False
    visiting = visiting if visiting is not None else set()
    if name in unrecognized_names:
        return True
    if name in visiting:
        return False
    visiting.add(name)
    return any(chain_hits_unrecognized(dep, universe, unrecognized_names, visiting)
               for dep in universe.ordered_deps(name))


# --------------------------------------------------------------------------
# Dataflow JSON loading
# --------------------------------------------------------------------------

def split_mashup_document(doc):
    """Split a pbi:mashup document string into {query_name: expression_text}."""
    queries = {}
    if not doc:
        return queries
    blocks = re.split(r'\nshared\s+', doc)
    for b in blocks[1:]:
        m = re.match(r'(#"(?:[^"\\]|\\.)*"|[A-Za-z_][\w\-]*)\s*=\s*(.*)', b, re.DOTALL)
        if not m:
            continue
        raw_name, expr = m.group(1), m.group(2)
        name = strip_quoted_ident(raw_name)
        expr = expr.rstrip()
        if expr.endswith(';'):
            expr = expr[:-1]
        queries[name] = expr
    # also handle blocks NOT prefixed with "shared " (private queries), e.g.
    # `#"Name" = let ... in ...;` appearing after a 'shared' one that itself
    # doesn't restart with shared. Power BI mashup docs normally prefix every
    # top-level query with "shared", so this is usually sufficient.
    return queries


_DUP_SUFFIX_RE = re.compile(r"^(.*?)\s*\((\d+)\)$")


def _dataflow_base_name(stem):
    """Strip a trailing ' (N)'/'(N)' suffix (Power BI's own auto-suffix for a
    duplicate-named published dataflow) to get the group's canonical name."""
    m = _DUP_SUFFIX_RE.match(stem)
    return m.group(1) if m else stem


def _dataflow_content_signature(raw):
    """Fingerprint used to tell a true duplicate export from a genuinely
    different dataflow - excludes 'modifiedTime' and the dataflow's own
    'name' field, since both always differ between two exports of the same
    dataflow (fresh timestamp, and Power BI auto-suffixes 'name' with '(N)'
    to avoid a display-name collision) even with zero real content change."""
    entities = sorted(raw.get("entities", []), key=lambda e: e.get("name", ""))
    return json.dumps(entities, sort_keys=True), raw.get("pbi:mashup", {}).get("document", "")


def _build_dataflow_entry(fp, raw):
    entities = {e["name"]: e for e in raw.get("entities", [])}
    doc = raw.get("pbi:mashup", {}).get("document", "")
    queries = split_mashup_document(doc)
    return {"entities": entities, "queries": queries, "path": fp, "raw": raw}


def _resolve_duplicate_group(base_name, files):
    """Load every file sharing one base name and collapse them to a single
    entry: the latest by modifiedTime. If the group's content genuinely
    differs (not just a harmless re-publish), attach a duplicate_notice so
    the report can flag it for manual confirmation instead of silently
    treating one of them as an unrelated 'ambiguous entity match'."""
    parsed = []
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                raw = json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Dataflow JSON file '{os.path.basename(fp)}' is corrupted or invalid: {e}")
            parsed.append({"path": fp, "raw": None, "error": f"JSON parse error: {e}", "is_critical": True})
            continue
        except FileNotFoundError:
            logger.error(f"Dataflow file not found: {fp}")
            parsed.append({"path": fp, "raw": None, "error": "File not found", "is_critical": True})
            continue
        except Exception as e:
            logger.error(f"Cannot read dataflow file '{os.path.basename(fp)}': {e}")
            parsed.append({"path": fp, "raw": None, "error": f"File read error: {e}", "is_critical": True})
            continue
        parsed.append({"path": fp, "raw": raw, "modifiedTime": raw.get("modifiedTime", "")})

    ok = [p for p in parsed if p["raw"] is not None]
    if not ok:
        bad = parsed[0]
        return _build_dataflow_entry(bad["path"], {}) | {"error": bad["error"]}

    ok.sort(key=lambda p: p["modifiedTime"])
    latest = ok[-1]
    entry = _build_dataflow_entry(latest["path"], latest["raw"])

    signatures = {_dataflow_content_signature(p["raw"]) for p in ok}
    if len(signatures) > 1:
        entry["duplicate_notice"] = {
            "base_name": base_name,
            "files": [os.path.basename(p["path"]) for p in ok],
            "chosen_file": os.path.basename(latest["path"]),
            "chosen_modified": latest["modifiedTime"],
        }
    else:
        print(f"NOTE: '{base_name}' has {len(ok)} duplicate exports with identical content; "
              f"using the latest ({os.path.basename(latest['path'])}).")
    return entry


def load_dataflows(folder):
    """Load every *.json under folder, searching all nested subfolders.
    Returns dict: base_name -> {"entities": {...}, "queries": {...}, "path": path,
    optionally "duplicate_notice": {...}}

    Files sharing a base name (e.g. 'X.json' and 'X (1).json' - Power BI's own
    auto-suffix for a duplicate-named published dataflow) are collapsed into a
    single entry: the latest by modifiedTime. This is resolved here, before
    entity_index is built, so a duplicate export never shows up as a separate
    "ambiguous entity match" candidate for unrelated cross-dataflow lookups.

    Raises RuntimeError if folder doesn't exist or no .json files are found anywhere,
    with helpful error messages.
    """
    if not os.path.isdir(folder):
        raise RuntimeError(
            f"Dataflow folder does not exist: '{folder}'\n"
            f"Check config.DATAFLOW_FOLDER path and ensure the folder is accessible."
        )

    files = glob.glob(os.path.join(folder, "**", "*.json"), recursive=True)
    if not files:
        raise RuntimeError(
            f"No dataflow .json files found in '{folder}' (including subfolders). "
            "Check that the folder contains the exported dataflow JSON files."
        )

    groups = {}
    for fp in files:
        stem = os.path.splitext(os.path.basename(fp))[0]
        base_name = _dataflow_base_name(stem)
        groups.setdefault(base_name, []).append(fp)

    dataflows = {}
    for base_name, group_files in groups.items():
        if len(group_files) == 1:
            fp = group_files[0]
            try:
                with open(fp, encoding="utf-8") as f:
                    raw = json.load(f)
            except json.JSONDecodeError as e:
                logger.error(f"Dataflow JSON file '{os.path.basename(fp)}' is corrupted or invalid: {e}")
                dataflows[base_name] = {"entities": {}, "queries": {}, "path": fp, "error": f"JSON parse error: {e}", "error_is_critical": True}
                continue
            except FileNotFoundError:
                logger.error(f"Dataflow file not found: {fp}")
                dataflows[base_name] = {"entities": {}, "queries": {}, "path": fp, "error": "File not found", "error_is_critical": True}
                continue
            except Exception as e:
                logger.error(f"Cannot read dataflow file '{os.path.basename(fp)}': {e}")
                dataflows[base_name] = {"entities": {}, "queries": {}, "path": fp, "error": f"File read error: {e}", "error_is_critical": True}
                continue
            dataflows[base_name] = _build_dataflow_entry(fp, raw)
        else:
            dataflows[base_name] = _resolve_duplicate_group(base_name, group_files)
    return dataflows



_entity_index_cache = None


def build_entity_index(dataflows):
    """Map entity_name -> list of stems where it's declared as a
    LocalEntity/CalculatedEntity (i.e. NOT a ReferenceEntity - a real,
    locally-defined publish target)."""
    index = {}
    for stem, df in dataflows.items():
        for ename, emeta in df["entities"].items():
            if emeta.get("$type") == "ReferenceEntity":
                continue
            index.setdefault(ename, []).append(stem)
    return index


def find_entity_across_dataflows(entity_name, dataflows, entity_index, exclude_stem=None):
    candidates = [s for s in entity_index.get(entity_name, []) if s != exclude_stem]
    return candidates


def build_name_index(dataflows):
    """Map a dataflow's own declared `name` field (and its filename stem, as
    a fallback) to its stem, for resolving in-document cross-dataflow jumps
    (PowerPlatform.Dataflows/Dataflows.Contents/PowerBI.Dataflows calls with
    a literal [dataflowName] filter) to the actual JSON file."""
    index = {}
    for stem, df in dataflows.items():
        raw = df.get("raw") or {}
        dname = raw.get("name")
        if dname:
            index.setdefault(dname, stem)
        index.setdefault(stem, stem)
    return index


def extract_dataflow_binding_strict(text, universe: Universe, guid_cache=None):
    """Like analyze_direct_dataflow_bindings' per-entry logic, but standalone
    and only returns a result when a concrete dataflow NAME can be resolved
    (used to detect in-document cross-dataflow jumps inside a dataflow's own
    M code, e.g. via PowerBI.Dataflows(null))."""
    guid_cache = guid_cache or {}
    if not RE_USES_DATAFLOW_CONNECTOR.search(text):
        return None
    local_map = _local_map_of(text)
    fields = extract_fields(text)
    ws_tokens = fields.get("workspaceName", [])
    df_tokens = fields.get("dataflowName", [])
    ws_ids = [unquote(t) for t in fields.get("workspaceId", [])]
    df_ids = [unquote(t) for t in fields.get("dataflowId", [])]
    entity_raw = fields.get("entity", [None])[0]
    entity = resolve_value_token(entity_raw, local_map, universe)

    def resolve_tok(tok):
        return resolve_value_token(tok, local_map, universe)

    ws_val = resolve_tok(ws_tokens[0]) if ws_tokens else None
    df_val = resolve_tok(df_tokens[0]) if df_tokens else None

    # Attempt GUID cache lookup with logging
    if not df_val and df_ids and ws_ids:
        cache_key = f"{ws_ids[0]}/{df_ids[0]}"
        if cache_key in guid_cache:
            df_val = guid_cache_dataflow_name(guid_cache, cache_key)
            ws_val = ws_val or guid_cache_workspace_name(guid_cache, cache_key)
            logger.debug(f"GUID cache hit: {cache_key} -> '{df_val}'")
        else:
            logger.debug(f"GUID cache miss: {cache_key} not in cache")

    if not ws_val and ws_ids:
        ws_val = f"(workspaceId GUID) {ws_ids[0]}"
    if not df_val and df_ids:
        df_val = f"(dataflowId GUID) {df_ids[0]}"
    if not df_val:
        return None
    return {
        "workspace": ws_val, "workspace_id": ws_ids[0] if ws_ids else None,
        "dataflow": df_val, "dataflow_id": df_ids[0] if df_ids else None,
        "entity": entity,
    }


def pick_best_candidate(candidates, from_stem, entity_name):
    if len(candidates) == 1:
        return candidates[0], False
    # disambiguate by longest common prefix with from_stem (family match)
    def common_prefix_len(a, b):
        n = 0
        for x, y in zip(a, b):
            if x != y:
                break
            n += 1
        return n
    ranked = sorted(candidates, key=lambda c: -common_prefix_len(c, from_stem))
    return ranked[0], True  # True = ambiguous (had to disambiguate)


# --------------------------------------------------------------------------
# Physical source extraction (within one dataflow's own query universe)
# --------------------------------------------------------------------------

def detect_connector(text):
    for label, pat in CONNECTOR_ORDER:
        if pat.search(text):
            return label
    return None


def extract_physical_details(connector_label, text, universe: Universe):
    details = {"connector": connector_label}
    if connector_label == "Oracle Database":
        m = RE_ORACLE.search(text)
        datamart = universe.resolve_literal(m.group(1)) if m else None
        schema = first_field_value(text, "Schema")
        table = first_field_value(text, "Item") or first_field_value(text, "Name")
        details.update({
            "datamart": datamart,
            "schema": schema,
            "table": table,
        })
        return details

    if connector_label in ("SharePoint Excel/CSV", "Excel Workbook"):
        site_m = RE_SHAREPOINT_SITE.search(text)
        site = universe.resolve_literal(site_m.group(1)) if site_m else None
        folder_m = RE_FOLDER_PATH_FILTER.search(text)
        folder = universe.resolve_literal(folder_m.group(1)) if folder_m else None
        if not folder:
            tc_m = RE_TEXT_CONTAINS_FOLDER.search(text)
            folder = tc_m.group(1) if tc_m else None
        name_m = RE_NAME_FILTER.search(text)
        file_name = name_m.group(1) if name_m else first_field_value(text, "Name")
        web_m = RE_WEB_CONTENTS.search(text)
        if not file_name and web_m:
            url = web_m.group(1)
            file_name = url.rsplit("/", 1)[-1]
            folder = folder or url.rsplit("/", 1)[0]
            site = site or url
        details.update({"site": site, "folder": folder, "file": file_name})
        return details

    if connector_label == "Csv Document":
        m = RE_CSV_DOCUMENT.search(text)
        param = universe.resolve_literal(m.group(1)) if m else None
        details.update({"file": param})
        return details

    if connector_label == "Web Contents":
        m = RE_WEB_CONTENTS.search(text)
        host = m.group(1).rstrip("/") if m else None
        relative_match = RE_WEB_RELATIVE_PATH.search(text)
        relative_path = relative_match.group(1) if relative_match else ""
        endpoint = f"{host}/{relative_path.lstrip('/')}" if host and relative_path else host
        parsed = urlsplit(endpoint or "")
        resource = parsed.path.rstrip("/").rsplit("/", 1)[-1] or None
        details.update({
            "host": host,
            "relative_path": relative_path or None,
            "endpoint": endpoint,
            "resource": resource,
            "source_system": "ServiceNow" if parsed.hostname and parsed.hostname.endswith(".service-now.com") else "REST API",
            "file": resource,
            "folder": host,
            "parser": "Json Document" if RE_JSON_DOCUMENT.search(text) else None,
        })
        return details

    return details


def resolve_within_universe(name, universe: Universe, stem, dataflows=None, entity_index=None, name_index=None,
                             entity_of=None, inherited_entity=None, inherited_table=None, inherited_schema=None,
                             visited=None, depth=0, guid_cache=None, max_depth=None):
    import config  # Import here to avoid circular imports
    if max_depth is None:
        max_depth = config.MAX_DEPENDENCY_DEPTH

    if visited is None:
        visited = set()
    if entity_of is None:
        entity_of = build_entity_of(universe)
    key = (stem, name)
    if key in visited:
        return {"unresolved": True, "reason": f"Circular dependency detected involving '{name}' in dataflow '{stem}'"}
    if depth > max_depth:
        logger.warning(f"Maximum dependency depth ({max_depth}) exceeded while resolving '{name}' in dataflow '{stem}'; stopping traversal")
        return {"unresolved": True, "reason": f"Exceeded max dependency depth ({max_depth}); check for undeclared cycles or extremely nested transformations"}
    visited.add(key)

    text = universe.get(name)
    if text is None:
        return {"unresolved": True, "reason": f"No M query named '{name}' found in dataflow '{stem}'."}

    my_entity = entity_of.get(name)
    effective_entity = my_entity or inherited_entity
    effective_table = first_field_value(text, "Name") or inherited_table
    effective_schema = first_field_value(text, "Schema") or inherited_schema

    members = extract_table_combine_members(text)
    if members:
        results = []
        for m_name in members:
            if m_name in universe:
                results.append((m_name, resolve_within_universe(
                    m_name, universe, stem, dataflows, entity_index, name_index, entity_of, effective_entity,
                    effective_table, effective_schema, visited, depth + 1, guid_cache)))
            else:
                results.append((m_name, {"unresolved": True, "reason": "union member not found in this dataflow"}))
        return {"union": True, "members": results, "raw": text}

    # cross-dataflow jump embedded directly in this dataflow's own M code
    # (e.g. `Source = PowerBI.Dataflows(null), ... [dataflowName] = PRM_X`) -
    # distinct from the JSON ReferenceEntity mechanism, but semantically the
    # same idea: hop to another dataflow file and keep resolving there.
    if dataflows is not None and name_index is not None:
        binding = extract_dataflow_binding_strict(text, universe, guid_cache)
        if binding and binding["dataflow"]:
            target_stem = name_index.get(binding["dataflow"])
            if target_stem and target_stem != stem:
                jump_entity = binding["entity"] or effective_entity or name
                sub = resolve_physical_source(target_stem, jump_entity, dataflows, entity_index,
                                               name_index=name_index, guid_cache=guid_cache, visited_pairs=None,
                                               hops=None)
                sub = dict(sub)
                sub["hops"] = [{"level": "jump", "from": stem, "to": target_stem, "entity": jump_entity,
                                "workspace": binding["workspace"]}] + sub.get("hops", [])
                if not sub.get("table") and effective_table:
                    sub["table"] = effective_table
                if not sub.get("table") and effective_entity:
                    sub["table"] = effective_entity
                if not sub.get("schema") and effective_schema:
                    sub["schema"] = effective_schema
                return sub
            elif not target_stem:
                reason = (f"Query '{name}' in dataflow '{stem}' references another dataflow "
                          f"named '{binding['dataflow']}' via M code, but no such dataflow file "
                          f"was found among the provided files.")
                return {"unresolved": True, "reason": reason}

    connector = detect_connector(text)
    if connector:
        details = extract_physical_details(connector, text, universe)
        if not details.get("table") and effective_table:
            details["table"] = effective_table
        if not details.get("table") and effective_entity:
            # Some Oracle-backed Level-2 dataflows list tables via a query
            # that renames the physical "Name" column to "entity"
            # (Table.RenameColumns(..., {{"Name", "entity"}})) and then
            # navigate via {[entity="X"]}[Data] - in that case the JSON
            # entity name IS the real physical table name.
            details["table"] = effective_entity
        if not details.get("schema") and effective_schema:
            details["schema"] = effective_schema
        return details

    # no connector call in this text -> recurse via nearest dependency
    deps = universe.ordered_deps(name)
    for dep in deps:
        sub = resolve_within_universe(dep, universe, stem, dataflows, entity_index, name_index, entity_of,
                                       effective_entity, effective_table, effective_schema, visited, depth + 1,
                                       guid_cache)
        if sub and not sub.get("unresolved"):
            return sub
    if deps:
        # all deps unresolved - return the first dep's failure for context
        return resolve_within_universe(deps[0], universe, stem, dataflows, entity_index, name_index, entity_of,
                                        effective_entity, effective_table, effective_schema, visited, depth + 1,
                                        guid_cache)
    return {"unresolved": True, "reason": f"Query '{name}' in dataflow '{stem}' has no recognizable source connector."}


def resolve_physical_source(stem, entity_name, dataflows, entity_index, name_index=None, guid_cache=None,
                             visited_pairs=None, hops=None):
    """Top-level resolver: given a Level-1 dataflow stem + entity name,
    determine whether it's a direct LocalEntity (resolve within stem) or a
    ReferenceEntity (jump to Level-2 dataflow), and return a dict with
    keys: level2_stem, level2_entity, ambiguous, plus the physical result
    (from resolve_within_universe / extract_physical_details / union)."""
    if visited_pairs is None:
        visited_pairs = set()
    if hops is None:
        hops = []
    guid_cache = guid_cache or {}

    if stem not in dataflows:
        return {"unresolved": True, "reason": f"Dataflow file for '{stem}' not found among provided files.", "hops": hops}

    pair = (stem, entity_name)
    if pair in visited_pairs:
        return {"unresolved": True, "reason": "cycle detected while following linked entities", "hops": hops}
    visited_pairs.add(pair)

    df = dataflows[stem]
    entity_meta = df["entities"].get(entity_name)

    if entity_meta is not None and entity_meta.get("$type") == "ReferenceEntity":
        candidates = find_entity_across_dataflows(entity_name, dataflows, entity_index, exclude_stem=stem)
        if not candidates:
            # Fallback: the entity's modelId is "{workspaceId}/{dataflowId}" -
            # look up the friendly dataflow name via guid_cache (populated
            # from the PowerBI service URL https://app.powerbi.com/groups/
            # {workspaceId}/dataflows/{dataflowId}, since that page requires
            # an authenticated session and can't be resolved by automated
            # fetch). If we know the name AND that dataflow is among the
            # provided files, jump straight to it.
            model_id = entity_meta.get("modelId", "")
            resolved_name = guid_cache_dataflow_name(guid_cache, model_id) if "/" in model_id else None
            if resolved_name and name_index and resolved_name in name_index:
                target_stem = name_index[resolved_name]
                hops = hops + [{"level": "level2-guid", "stem": target_stem, "entity": entity_name,
                                 "resolved_via": "PowerBI service GUID lookup", "dataflow_name": resolved_name}]
                return resolve_physical_source(target_stem, entity_name, dataflows, entity_index, name_index,
                                                guid_cache, visited_pairs, hops)
            reason = (f"Entity '{entity_name}' in dataflow '{stem}' is a linked/reference entity "
                      f"but no dataflow file among the provided set publishes a local entity with "
                      f"that name (the source dataflow is likely not among the provided files).")
            if "/" in model_id:
                ws_id, df_id = model_id.split("/", 1)
                if resolved_name:
                    reason += (f" Resolved dataflow name via PowerBI service GUID lookup: '{resolved_name}', "
                               f"but no local file with that name was found among the provided dataflow JSON files.")
                else:
                    reason += (f" Dataflow name unknown - see {powerbi_service_url(ws_id, df_id)} "
                               f"(requires sign-in) and add the resolved name to guid_dataflow_names.json "
                               f"under key '{model_id}' to enable automatic resolution.")
            return {
                "unresolved": True,
                "reason": reason,
                "hops": hops,
                "access_request": {
                    "workspace_id": ws_id if "/" in model_id else None,
                    "workspace": guid_cache_workspace_name(guid_cache, model_id) if "/" in model_id else None,
                    "dataflow_id": df_id if "/" in model_id else None,
                    "dataflow": resolved_name,
                    "entity": entity_name,
                },
            }
        chosen, ambiguous = pick_best_candidate(candidates, stem, entity_name)
        hops = hops + [{"level": "level2", "stem": chosen, "entity": entity_name, "ambiguous": ambiguous,
                         "all_candidates": candidates}]
        return resolve_physical_source(chosen, entity_name, dataflows, entity_index, name_index, guid_cache,
                                        visited_pairs, hops)

    # Local (or unknown-in-entities-list) resolution within this dataflow's own queries
    universe = Universe(df["queries"])
    entity_of = build_entity_of(universe)
    result = resolve_within_universe(entity_name, universe, stem, dataflows, entity_index, name_index, entity_of,
                                      guid_cache=guid_cache)
    result["hops"] = hops + result.get("hops", [])
    result["resolved_stem"] = stem
    return result
