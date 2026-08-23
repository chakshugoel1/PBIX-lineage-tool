"""
config.py

Central place for every machine/project-specific path used by this tool.
To point the pipeline at a different PBIX file, dataflow export, or target
workbook (e.g. on a new machine, or for a different report), edit the
values below - nothing else in the codebase needs to change.

All paths are resolved relative to this file's own directory, so the
project can live anywhere on disk (or on a different machine/drive) and
still work without edits, as long as the input files below keep the same
names/relative layout.
"""
import os
import re

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def _p(*parts):
    return os.path.join(PROJECT_ROOT, *parts)


# --- Inputs ---------------------------------------------------------------
PBIX_PATH = _p("DTS - CashPlus-Dashboard (1).pbix")
DATAFLOW_FOLDER = _p("PowerBIDataflows")
TARGET_XLSX = _p("CASHPLUS-DASHBOARD 1.xlsx")

def pbix_stem(pbix_path):
    """Filesystem-safe stem derived from a PBIX file's basename, for naming output reports."""
    return re.sub(r"[^A-Za-z0-9]+", "_", os.path.splitext(os.path.basename(pbix_path))[0]).strip("_")


_PBIX_STEM = pbix_stem(PBIX_PATH)

# --- Configuration --------------------------------------------------------
# Maximum allowed depth when recursively resolving M query dependencies.
# Protects against stack overflow on cyclic/deeply-nested transformations.
MAX_DEPENDENCY_DEPTH = 25

# Timeout (seconds) for PowerShell dataflow export subprocess.
POWERSHELL_EXPORT_TIMEOUT = 600  # 10 minutes

# --- Outputs ----------------------------------------------------------------
GENERATED_XLSX = _p(f"Generated_{_PBIX_STEM}_Lineage.xlsx")
COMPARISON_XLSX = _p(f"Final_Source_Comparison_{_PBIX_STEM}.xlsx")

# Companion report: PBIX -> Dataflow -> Physical Source lineage in the
# "Table Lineage" / "Overview" column layout (see
# build_dataflow_table_lineage_report.py). This is the authoritative,
# self-contained report for future PBIX files where no target/reference
# workbook exists to validate against.
DATAFLOW_LINEAGE_XLSX = _p(f"Dataflow_Table_Lineage_Report_{_PBIX_STEM}.xlsx")
