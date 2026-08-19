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

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def _p(*parts):
    return os.path.join(PROJECT_ROOT, *parts)


# --- Inputs ---------------------------------------------------------------
PBIX_PATH = _p("DTS - CashPlus-Dashboard (1).pbix")
DATAFLOW_FOLDER = _p("PowerBIDataflows")
TARGET_XLSX = _p("CASHPLUS-DASHBOARD 1.xlsx")

# --- Outputs ----------------------------------------------------------------
GENERATED_XLSX = _p("Generated_CashPlus_Lineage.xlsx")
COMPARISON_XLSX = _p("Final_Source_Comparison.xlsx")

# Companion report: PBIX -> Dataflow -> Physical Source lineage in the
# "Table Lineage" / "Overview" column layout (see
# build_dataflow_table_lineage_report.py). This is the authoritative,
# self-contained report for future PBIX files where no target/reference
# workbook exists to validate against.
DATAFLOW_LINEAGE_XLSX = _p("Dataflow_Table_Lineage_Report.xlsx")
