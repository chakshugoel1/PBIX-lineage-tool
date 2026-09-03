# Code Review: Edge Cases & Quality Analysis

**Date**: 2026-08-23  
**Test Results**: 21/21 tests passing ✓  
**End-to-End Pipeline**: ✓ Working (173 table rows processed successfully)

---

## Critical Edge Cases Identified

### 1. **CRITICAL: Empty or Null String Handling in Regex/Parsing**

**Location**: `lineage_lib.py` - Multiple functions (`unquote()`, `resolve_value_token()`, `extract_fields()`)

**Issue**: Several parsing functions accept `None` or empty strings but lack explicit null-checks at all call sites.

**Examples**:
- `unquote(token)` checks `if token is None` but called with raw field values that could be `None` without caller validation
- `extract_fields(text)` assumes `text` is a string - if passed `None`, `.finditer()` will crash
- `resolve_value_token()` has multiple branches returning `None` implicitly (no explicit return)

**Impact**: 
- Crashes on malformed PBIX files with missing/corrupt M query text
- Silent `None` returns can propagate into report cells without detection

**Proposed Solution**:
```python
def unquote(token):
    if token is None or not isinstance(token, str):
        return None
    token = token.strip()
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1].replace('\\"', '"')
    return None  # explicit return instead of implicit

def extract_fields(text):
    """Add guard clause at start"""
    if not text or not isinstance(text, str):
        return {}
    # ... rest of function

def resolve_value_token(token, local_map, universe, global_params=None):
    """Explicit None checks before all branches"""
    if token is None:
        return None
    if not isinstance(token, str):
        return None
    # ... rest of function
```

**Risk Level**: HIGH - Can cause crashes on edge-case PBIX files

---

### 2. **CRITICAL: Missing Exception Handling in JSON Loading**

**Location**: `lineage_lib.py` - `load_dataflows()`, `_resolve_duplicate_group()`

**Issue**: JSON file parsing wrapped in try/except, but only returns empty entities/queries dict on failure - no validation that critical data was lost.

**Code**:
```python
try:
    raw = json.load(open(fp, encoding="utf-8"))
except Exception as e:
    dataflows[base_name] = {"entities": {}, "queries": {}, "path": fp, "error": str(e)}
    continue  # silently continues with empty dataflow!
```

**Impact**:
- Corrupted/truncated dataflow JSON silently treated as empty dataflow
- Report will show "entity not found" for tables actually using that dataflow
- User has no way to know if a dataflow file is corrupt vs. genuinely unused

**Proposed Solution**:
```python
try:
    raw = json.load(open(fp, encoding="utf-8"))
except json.JSONDecodeError as e:
    # Invalid JSON - flag it prominently
    print(f"ERROR: Dataflow JSON file '{fp}' is corrupted or invalid: {e}")
    dataflows[base_name] = {
        "entities": {}, "queries": {}, "path": fp, 
        "error": f"JSON parse error: {e}",
        "error_is_critical": True  # new flag
    }
except Exception as e:
    # File not readable
    print(f"ERROR: Cannot read dataflow file '{fp}': {e}")
    dataflows[base_name] = {
        "entities": {}, "queries": {}, "path": fp,
        "error": f"File read error: {e}",
        "error_is_critical": True
    }
    continue
```

Then in `build_lineage_report.py`, check for `error_is_critical` flag and alert user to review.

**Risk Level**: HIGH - Silent data loss / incorrect reports

---

### 3. **HIGH: Unbounded Depth Limit in Recursive Dependency Resolution**

**Location**: `lineage_lib.py` - `resolve_within_universe()`

**Issue**: Depth limit is hardcoded to 25:
```python
if visited is None:
    visited = set()
if entity_of is None:
    entity_of = build_entity_of(universe)
key = (stem, name)
if key in visited or depth > 25:  # <-- Magic number, no config
    return {"unresolved": True, "reason": "cycle or depth-limit reached"}
```

**Impact**:
- Legitimate deeply-nested dependency chains (real use cases in complex ETLs) may be prematurely cut off
- No visibility into whether cutoff was due to actual cycle vs. arbitrary depth limit
- No way for users to adjust depth limit without editing code

**Proposed Solution**:
```python
# In config.py, add:
MAX_DEPENDENCY_DEPTH = 25  # configurable

# In lineage_lib.py:
def resolve_within_universe(name, universe: Universe, stem, dataflows=None, 
                            entity_index=None, name_index=None, entity_of=None, 
                            inherited_entity=None, inherited_table=None, 
                            inherited_schema=None, visited=None, depth=0, 
                            guid_cache=None, max_depth=None):
    if max_depth is None:
        max_depth = config.MAX_DEPENDENCY_DEPTH
    
    if visited is None:
        visited = set()
    if key in visited:
        return {"unresolved": True, "reason": "cycle detected"}
    if depth > max_depth:
        return {"unresolved": True, 
                "reason": f"exceeded max dependency depth ({max_depth}); "
                          "check for undeclared cycles or extremely nested transformations"}
```

**Risk Level**: MEDIUM - Affects only complex ETLs with deep nesting

---

### 4. **HIGH: No Validation of Dataflow Folder Path**

**Location**: `config.py`, `build_lineage_report.py` - `load_everything()`

**Issue**: `load_dataflows()` raises `RuntimeError` if no JSON files found, but only AFTER attempting to open the entire folder without checking if it exists first.

```python
def load_dataflows(folder):
    files = glob.glob(os.path.join(folder, "**", "*.json"), recursive=True)
    if not files:
        raise RuntimeError(...)  # <-- Error message is good, but delayed
```

**Impact**:
- If `config.DATAFLOW_FOLDER` path is wrong (typo, moved folder), user gets cryptic error after waiting for PBIX load
- No early warning about misconfigured path

**Proposed Solution**:
```python
def load_dataflows(folder):
    """Load every *.json under folder, searching all nested subfolders."""
    # Validate folder exists BEFORE attempting glob
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
    # ... rest of function
```

**Risk Level**: MEDIUM - Usability issue, not a correctness issue

---

### 5. **HIGH: No Validation of PBIX File Path**

**Location**: `build_lineage_report.py` - `load_everything()`

**Issue**: `PBIXRay(pbix_path)` is called without checking if file exists first.

```python
def load_everything(pbix_path=None, dataflow_folder=None):
    pbix_path = pbix_path or PBIX_PATH
    dataflow_folder = dataflow_folder or DATAFLOW_FOLDER
    model = PBIXRay(pbix_path)  # <-- May fail silently if file doesn't exist
```

**Impact**:
- If PBIX file is moved/deleted, error occurs deep in PBIXRay's C++ binding layer
- No user-friendly error message

**Proposed Solution**:
```python
def load_everything(pbix_path=None, dataflow_folder=None):
    pbix_path = pbix_path or PBIX_PATH
    dataflow_folder = dataflow_folder or DATAFLOW_FOLDER
    
    if not os.path.isfile(pbix_path):
        raise FileNotFoundError(
            f"PBIX file not found: '{pbix_path}'\n"
            f"Check config.PBIX_PATH and ensure the file exists."
        )
    
    model = PBIXRay(pbix_path)
```

**Risk Level**: MEDIUM - Usability issue

---

### 6. **MEDIUM: Regex Patterns Don't Account for Whitespace Variations**

**Location**: `lineage_lib.py` - Multiple regex patterns

**Issue**: Patterns like `RE_ORACLE`, `RE_SHAREPOINT_SITE`, etc. assume specific spacing around function calls:

```python
RE_ORACLE = re.compile(r'Oracle\.Database\(\s*(#"[^"]+"|"[^"]*"|[A-Za-z_][\w]*)')
RE_SHAREPOINT_SITE = re.compile(r'SharePoint\.Files\(\s*(#"[^"]+"|"[^"]*")')
```

**Impact**:
- M code with line breaks: `Oracle.Database(\n #"Server"...)` won't match
- Comments inside connectors: `Oracle.Database(/* comment */ #"Server"...)` won't match
- Different formatting styles can cause silent miss

**Proposed Solution**:
```python
# Use re.DOTALL and \s* more liberally
RE_ORACLE = re.compile(
    r'Oracle\.Database\s*\(\s*'  # Allow whitespace after function name
    r'(#"[^"]+"|"[^"]*"|[A-Za-z_][\w]*)',
    re.IGNORECASE
)

# Better: compile all patterns with flag for multiline handling
RE_ORACLE = re.compile(
    r'Oracle\.Database\s*\(\s*'
    r'(#"[^"]+"|"[^"]*"|[A-Za-z_][\w]*)',
    re.MULTILINE | re.VERBOSE  # re.VERBOSE allows pattern comments
)
```

**Risk Level**: MEDIUM - Rare, but causes silent misses

---

### 7. **MEDIUM: No Handling of Circular References in Dependencies**

**Location**: `lineage_lib.py` - `resolve_pbix_lineage()`, `resolve_within_universe()`

**Issue**: Both functions attempt cycle detection via `visiting` set, but:
- Cycle detection only works within one traversal (not pre-computed)
- If two independent paths both lead to the same cycle, performance degrades
- No user visibility into which queries are involved in the cycle

**Example**:
```
Table A -> Query X -> Query Y
Table B -> Query Y -> Query X (cycle!)
Table C -> Query Z -> Query X (hits cycle again)
```

When resolving Table C, the algorithm re-walks the cycle even though it was already detected.

**Proposed Solution**:
```python
def _find_cycles_in_universe(universe: Universe):
    """Pre-compute strongly connected components using Tarjan's algorithm."""
    cycles = []  # List of [query_names] that form cycles
    # ... implementation
    return cycles

# Then in resolve_pbix_lineage/resolve_within_universe:
if name in universe.known_cycles:
    cycle_members = [q for cycle in universe.known_cycles if name in cycle][0]
    return {
        "unresolved": True,
        "reason": f"Circular dependency detected: {' <- '.join(cycle_members)}"
    }
```

**Risk Level**: LOW - Only affects complex dataflows with cycles (rare)

---

### 8. **MEDIUM: GUID Cache Lookups Not Logged**

**Location**: `lineage_lib.py` - `analyze_direct_dataflow_bindings()`, `extract_dataflow_binding_strict()`

**Issue**: When a GUID-only dataflow reference is resolved via `guid_cache`, there's no logged indication of:
- Whether cache hit succeeded or failed
- What GUIDs were looked up
- Whether the returned name is stale/outdated

```python
if not df_val and df_ids and ws_ids and f"{ws_ids[0]}/{df_ids[0]}" in guid_cache:
    df_val = guid_cache[f"{ws_ids[0]}/{df_ids[0]}"]  # <-- Silent lookup
```

**Impact**:
- Users can't audit which references were resolved via cache vs. direct literal
- If cache is stale/corrupted, no indication

**Proposed Solution**:
```python
if not df_val and df_ids and ws_ids:
    cache_key = f"{ws_ids[0]}/{df_ids[0]}"
    if cache_key in guid_cache:
        df_val = guid_cache[cache_key]
        print(f"DEBUG: GUID cache hit: {cache_key} -> '{df_val}'")
    else:
        print(f"DEBUG: GUID cache miss: {cache_key} not in cache")
        
# Better: add a 'resolution_method' field tracking this
direct[name] = {
    ...,
    "resolution_method": "guid_cache" if cache_hit else "direct",
    "guid_lookup_key": cache_key if cache_key else None
}
```

**Risk Level**: LOW - Debugging issue, not correctness issue

---

### 9. **MEDIUM: No Handling of Empty M Query Expressions**

**Location**: `lineage_lib.py` - `Universe.get()`, `resolve_pbix_lineage()`

**Issue**: If a PBIX contains a table with an empty M query expression (corrupted export?), the code doesn't explicitly handle it:

```python
def get(self, name):
    return self.texts.get(name)  # Could return empty string ""

# Later:
text = universe.get(name)
if not RE_USES_DATAFLOW_CONNECTOR.search(text):  # Works with ""
    continue
```

**Impact**:
- Empty expressions are silently treated as "no dataflow connector"
- User can't distinguish between "intentional empty table" vs. "corrupted export"

**Proposed Solution**:
```python
def get(self, name):
    value = self.texts.get(name)
    if value == "":
        print(f"WARNING: Query/table '{name}' has empty M expression")
    return value

# In resolve_pbix_lineage:
text = universe.get(name)
if not text:
    return None  # Explicit handling of empty/None
```

**Risk Level**: LOW - Rare corrupted file scenario

---

### 10. **MEDIUM: No Timeout on PowerShell Dataflow Export**

**Location**: `dataflow_export.py` - `export_all_dataflows()`

**Issue**: PowerShell subprocess is started with no timeout:

```python
proc = subprocess.Popen(
    [...],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)  # <-- No timeout!

for line in proc.stdout:  # <-- Could hang indefinitely
    ...
```

**Impact**:
- If PowerShell export gets stuck (network issue, permission prompt), GUI freezes forever
- User must force-kill application

**Proposed Solution**:
```python
import signal

def export_all_dataflows(..., timeout_seconds=600):  # 10 min default
    """..."""
    emit = progress_cb or (lambda line: None)
    
    try:
        proc = subprocess.Popen(
            [...],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
    except FileNotFoundError:
        return False, "PowerShell is not available on this machine."
    
    if proc_holder is not None:
        proc_holder.append(proc)
    
    try:
        # Use thread-based timeout
        result = None
        start_time = time.time()
        
        for line in proc.stdout:
            if time.time() - start_time > timeout_seconds:
                proc.kill()
                return False, f"Export timed out after {timeout_seconds} seconds"
            
            line = line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith(RESULT_PREFIX):
                try:
                    result = json.loads(line[len(RESULT_PREFIX):])
                except json.JSONDecodeError:
                    pass
            else:
                emit(line)
        proc.wait()
    except Exception as e:
        proc.kill()
        return False, f"Export process error: {e}"
```

**Risk Level**: MEDIUM - GUI usability issue

---

### 11. **LOW: Filename Sanitization Doesn't Handle Very Long Names**

**Location**: `fileutils.py` - `sanitize_filename()`

**Issue**: Windows filenames are limited to 255 characters, but sanitization doesn't truncate:

```python
def sanitize_filename(name):
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip().strip(".")
    return cleaned or "export"  # <-- No length check
```

**Impact**:
- Very long entity/table names could produce files that Windows can't create
- Silent failure when writing output files

**Proposed Solution**:
```python
def sanitize_filename(name, max_length=255):
    """Replace invalid chars and truncate to Windows limit."""
    cleaned = _INVALID_FILENAME_CHARS.sub("_", name).strip().strip(".")
    cleaned = cleaned or "export"
    
    if len(cleaned) > max_length:
        # Truncate, but try to preserve a suffix if it's readable
        ext = ""  # would extract actual extension if present
        base_max = max_length - len(ext) - 1  # -1 for safety margin
        cleaned = cleaned[:base_max] + ext
        if cleaned != name:
            print(f"WARNING: Filename truncated: '{name[:50]}...' -> '{cleaned[:50]}...'")
    
    return cleaned
```

**Risk Level**: LOW - Rare edge case

---

### 12. **LOW: No Validation of Excel Output Path**

**Location**: `build_lineage_report.py` - `write_workbook()`

**Issue**: Output path is written directly without checking if parent directory exists:

```python
def write_workbook(rows, ctx, output_path=None):
    output_path = output_path or OUTPUT_PATH
    wb = openpyxl.Workbook()
    # ...
    wb.save(output_path)  # <-- May fail if parent dir doesn't exist
```

**Impact**:
- If `OUTPUT_PATH` parent folder is deleted, write fails with cryptic openpyxl error
- No friendly error message

**Proposed Solution**:
```python
def write_workbook(rows, ctx, output_path=None):
    output_path = output_path or OUTPUT_PATH
    output_dir = os.path.dirname(output_path)
    
    if output_dir and not os.path.isdir(output_dir):
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            raise IOError(f"Cannot create output directory '{output_dir}': {e}")
    
    wb = openpyxl.Workbook()
    # ... (rest of function)
    
    try:
        wb.save(output_path)
    except Exception as e:
        raise IOError(f"Cannot write to '{output_path}': {e}")
```

**Risk Level**: LOW - Usability issue

---

## Summary Table

| # | Issue | Risk | Category | Impact | Effort |
|---|-------|------|----------|--------|--------|
| 1 | Null/empty string handling | HIGH | Stability | Crash risk | Medium |
| 2 | Missing JSON error validation | HIGH | Correctness | Silent data loss | Medium |
| 3 | Hardcoded depth limit | HIGH | Completeness | Incomplete lineage | Low |
| 4 | No dataflow path validation | MEDIUM | Usability | Delayed errors | Low |
| 5 | No PBIX path validation | MEDIUM | Usability | Poor error messages | Low |
| 6 | Whitespace in regex patterns | MEDIUM | Correctness | Silent misses | Medium |
| 7 | Circular reference handling | MEDIUM | Performance | Slowdowns | Medium |
| 8 | GUID cache not logged | MEDIUM | Debugging | Audit trail missing | Low |
| 9 | No empty query handling | MEDIUM | Robustness | Confusing results | Low |
| 10 | No PowerShell timeout | MEDIUM | Usability | GUI freezes | Medium |
| 11 | Long filename not truncated | LOW | Edge case | Rare failure | Low |
| 12 | No output dir validation | LOW | Usability | Cryptic errors | Low |

---

## Testing Recommendations

To validate edge cases once solutions are implemented:

1. **Test 1-2**: Create intentionally malformed PBIX files with:
   - Missing M query expressions
   - Null/empty connection strings
   - Corrupt dataflow JSON files

2. **Test 3**: Create PBIX with 30+ nested dependencies to test depth limit

3. **Test 4-5**: Misconfigure paths in `config.py` and verify error messages

4. **Test 6**: Create M code with unusual whitespace (line breaks, comments) and verify pattern matching

5. **Test 7**: Create deliberately circular dependencies in M code

6. **Test 10**: Mock PowerShell hanging and verify timeout works

7. **Test 11**: Create entity name with 300+ characters

8. **Test 12**: Delete output directory before running report

---

## Recommended Priority Order

**Phase 1 (Critical - Do First)**:
1. Edge case #1: Null/empty string handling
2. Edge case #2: JSON error validation  
3. Edge case #4-5: Path validation

**Phase 2 (Important - Do Next)**:
4. Edge case #3: Configurable depth limit
5. Edge case #10: PowerShell timeout
6. Edge case #6: Regex whitespace handling

**Phase 3 (Nice-to-Have)**:
7. Edge cases #7-9, #11-12: Remaining improvements

