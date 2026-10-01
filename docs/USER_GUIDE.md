# PBIX Lineage Tool User Guide

This guide explains what each tab does and which files are needed. The tool is a Windows desktop application for analyzing Power BI reports and their data sources.

## Before you start

- Use a Windows machine with the tool installed.
- Keep client PBIX files and generated workbooks in a protected location.
- For source lineage, prepare a folder containing exported dataflow JSON files.
- For model change impact, prepare both the original and changed PBIX files.
- For baseline estimation, optionally prepare a completed `Requirements` workbook using the [requirements guide](REQUIREMENTS_TEMPLATE_GUIDE.md) and [template](../templates/Requirements_Template.xlsx).

## Run

### What it does

The Run tab follows tables in a PBIX report through Power Query and Power BI dataflows to the final physical source, such as an Oracle table, SharePoint file, Excel file, CSV, or web source.

### Inputs

- **PBIX file**: the Power BI report to analyze.
- **Dataflow folder**: the folder containing exported dataflow `.json` files.
- **Output folder**: where the Excel reports are written. If blank, the PBIX folder is used.
- **Keep a timestamped copy**: archives older generated reports before writing new ones.
- **Ask Power BI for unknown dataflow names**: use only when local JSON files cannot identify a referenced GUID. This requires Power BI sign-in.

### Results

The tab creates a main lineage workbook and a companion dataflow-table workbook. The result cards show resolved, manual-review, unresolved, access-required, and calculated/no-query tables. Rows under **Needs Manual Review** require investigation because the source was ambiguous, unavailable, or could not be resolved automatically.

## Dataflow Export

### What it does

This tab downloads every dataflow definition from a Power BI workspace into separate local JSON files. It is normally used before running the Run tab.

### Inputs

- **Workspace ID**: the Power BI workspace GUID.
- **Output folder**: where the exported JSON files are saved.

Click **Run Export**, complete the interactive Power BI sign-in if requested, and wait for the export summary. The exported folder can then be selected in the Run tab.

## Model Change Impact

### What it does

This tab compares two PBIX files:

- **Baseline PBIX**: the original report.
- **Changed PBIX**: the proposed or newer report.

It reports added, removed, renamed, and modified tables, columns, measures, and relationships. It then follows DAX dependencies to identify affected report visuals and detects broken bindings to removed objects.

### How to read the output

- **Direct impact**: the visual is bound directly to the changed object.
- **Transitive/dependency impact**: the visual depends on the changed object through another measure or calculated object.
- **Broad impact**: a table or relationship change may affect many objects and needs manual review.
- **Dangling binding**: a visual still refers to an object removed from the changed report.

The output is a `Model_Change_Impact` Excel workbook containing summaries and before/after details. The tool analyzes metadata and bindings; it does not render visuals or execute DAX.

## Baseline Estimation

### What it does

This tab analyzes one existing PBIX before development starts. It creates a visual-level inventory and an RTM-style workbook showing which pages, visuals, and model objects are in scope.

### Inputs

- **PBIX file**: the existing report.
- **Output folder**: where the baseline workbook is written.
- **Requirements Excel**: optional workbook prepared using the [requirements guide](REQUIREMENTS_TEMPLATE_GUIDE.md).

Without a requirements workbook, the tool creates generated requirement numbers for the report visuals. With one, it maps requirements to pages, visual names, or Visual IDs and expands the mapping through model dependencies.

### Output sheets

- **RTM**: one planning row per real report visual.
- **Requirement Copy**: the supplied requirements copied into the output.
- **Visual Impact by Requirement**: mapped requirements and their affected visuals.

`Production` means the current requirement for a visual. Older rows for the same visual may be marked `Obsolete`. Review warnings when one requirement maps to nothing or when a visual is assigned to multiple requirements.

## About

The About tab shows the installed version, theme control, update status, and Hard Reset action. It also provides direct access to this guide, the requirements guide, and the Excel template.

Use **Check for Updates** when the application reports that a newer version is available. Use **Hard Reset App** only when a run is stuck or the application needs a clean restart.

## Common workflow

1. Use **Dataflow Export** to refresh local dataflow JSON files.
2. Use **Run** to trace a PBIX report to physical sources.
3. Use **Baseline Estimation** before changing a report.
4. Use **Model Change Impact** after creating a changed PBIX.
5. Open flagged rows and broad impacts for manual review before approving a change.

## Important limitations

- Live PBIX extraction, Power BI sign-in, and PowerShell export require authorized local access.
- A resolved result is evidence from available metadata, not a guarantee that the source is currently accessible.
- Broad table and relationship impacts are intentionally conservative.
- Generated workbooks may contain client information and should be handled accordingly.