"""Compatibility entry point for reporting.lineage_report."""
from reporting.lineage_report import *

if __name__ == "__main__":
    from reporting.lineage_report import build_report, write_workbook
    from reporting import dataflow_table_report

    rows, ctx = build_report()
    write_workbook(rows, ctx)
    dataflow_table_report.build_and_save(ctx)
