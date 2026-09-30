# Workspace File Guide

This lists the purpose of each application file and groups data-heavy/generated collections by role. It reflects the current workspace, including local untracked work; it is not a list of files safe to delete. See [Architecture](ARCHITECTURE.md) for written workflow steps.

## Root: entry points and setup

| File | Purpose |
|---|---|
| `app.py` | Desktop entry point; configures logging and Windows app identity, then launches the GUI. |
| `build_lineage_report.py` | Script/compatibility entry point for the main and companion lineage workbooks. |
| `build_dataflow_table_lineage_report.py` | Compatibility entry point for the table-lineage workbook. |
| `build_transformations_report.py` | Compatibility import for the transformation-report implementation. |
| `compare_final_source_table.py` | Optional legacy comparison against a reference workbook. |
| `legacy_cli.py` | Compatibility entry point to the CLI implementation. |
| `lineage_lib.py` | Compatibility import for the lineage engine. |
| `dataflow_export.py` | Compatibility import for the dataflow export service. |
| `fileutils.py` | Compatibility import for file utilities. |
| `config.py` | Default input/output paths and naming helpers for script workflows. |
| `version.py` | Application version information. |
| `Install.cmd` | Installer launcher for end users. |
| `bootstrap-install.ps1` | Installer bootstrap/update and shortcut setup. |
| `setup.ps1` | Local developer environment setup. |
| `requirements.txt` | Runtime Python dependencies. |
| `requirements-dev.txt` | Development/test dependencies. |
| `Icon.ico`, `Icon.png` | Application and installer icon assets. |
| `.gitignore` | Excludes local environments, client inputs, exports, PBIP assets, and generated reports. |
| `README.md` | Installation, usage, and workflow overview. Some Baseline Estimation details predate the current three-sheet RTM output; use the RTM context for that contract. |

## Root: local inputs and outputs

| File or group | Purpose |
|---|---|
| `guid_dataflow_names.json` | Local cache/mapping for GUID-only dataflow references; not safely reproducible in every environment. |
| `visual_descriptions.json` | Local cache of visual descriptions and history for the current output folder. |
| `Baseline_Estimation_*.xlsx` | Generated baseline/RTM workbook. |
| `Generated_*_Lineage.xlsx` | Generated main source-lineage workbook. |
| `Dataflow_Table_Lineage_Report_*.xlsx` | Generated companion table-lineage workbook. |
| `CASHPLUS-DASHBOARD 1.xlsx` | Local reference workbook for optional comparison. |
| `Review_Transformations_Real_Data.xlsx` | Local transformation review workbook. |
| `*.pbix` | Local report inputs; large, potentially sensitive, and ignored by Git. |
| `CygnusIN.pbip` | Power BI project entry point for the report/model folders. |
| `PBIX -> Dataflow -> Source Lineage Project Status.docx` | Local project-status document (the on-disk filename uses arrow characters). |
| `_compare_grains.py` | Untracked local diagnostic script; review before retention or removal. |

## Application packages

`__init__.py` in each package marks its directory as an importable Python package.

| File | Purpose |
|---|---|
| `cli/legacy_cli.py` | Command-line lineage workflow implementation. |
| `core/lineage_lib.py` | Resolves M query/dataflow dependency chains to physical sources and caches GUID matches. |
| `gui/main_window.py` | Tabs, inputs, progress, and result presentation. |
| `gui/worker.py` | Background Qt workers for lineage, export, baseline estimation, impact, and updates. |
| `gui/settings.py` | Persists GUI preferences. |
| `gui/updater.py` | Checks for and installs application updates; manages shortcut identity/icon. |
| `reporting/lineage_report.py` | Main lineage pipeline and Excel writer. |
| `reporting/dataflow_table_report.py` | Companion table-grain lineage workbook. |
| `reporting/transformations_report.py` | M-step transformation narration and sheet output. |
| `reporting/comparison_report.py` | Reference-versus-generated source comparison. |
| `services/dataflow_export.py` | Orchestrates Power BI dataflow export and GUID mapping persistence. |
| `services/fileutils.py` | Output path and archive helpers. |
| `services/guid_resolver.py` | Optional online resolution of unknown dataflow GUIDs. |
| `powershell/Export-AllDataflows.ps1` | Power BI API export of workspace dataflows. |
| `powershell/Resolve-DataflowNames.ps1` | Power BI lookup of GUID-only dataflow names. |

## Model change impact package

| File | Purpose |
|---|---|
| `model_change_impact/snapshot.py` | Reads PBIX semantic-model metadata into a comparable snapshot. |
| `model_change_impact/report_layout.py` | Extracts pages, visuals, filters, bindings, and layout changes from PBIX reports. |
| `model_change_impact/diff.py` | Compares model snapshots and identifies changed objects/rename candidates. |
| `model_change_impact/impact.py` | Follows DAX dependents and visual bindings to assess downstream impact. |
| `model_change_impact/excel_report.py` | Writes model-change summary and changed-object sheets. |
| `model_change_impact/baseline_estimation.py` | Produces one-PBIX RTM, requirement copy, and requirement-to-visual output. |
| `model_change_impact/requirements.py` | Reads and validates optional requirements workbooks. |
| `model_change_impact/requirement_mapping.py` | Maps requirements to visuals and dependencies. |
| `model_change_impact/requirement_report.py` | Requirement-oriented report helpers. |
| `model_change_impact/visual_descriptions.py` | Deterministic visual description generation and cache/history handling. |
| `model_change_impact/history_store.py` | Persists/retrieves change history used by the model-impact workflow. |
| `model_change_impact/autodetect.py` | Detects/labels relationships inferred from metadata. |

## Tests and diagnostic tools

| File | Purpose |
|---|---|
| `tests/conftest.py` | Shared pytest fixtures/configuration. |
| `tests/test_access_required.py` | Access-required lineage cases. |
| `tests/test_app_icon.py` | App icon/shortcut checks. |
| `tests/test_baseline_estimation.py` | Baseline and RTM workbook behavior. |
| `tests/test_dataflow_export.py` | Dataflow export service behavior. |
| `tests/test_e2e_workflows.py` | Offline worker-to-file tests for lineage, export, baseline, and model impact. |
| `tests/test_edge_cases.py` | Source resolution edge cases. |
| `tests/test_fileutils.py` | Output file utility behavior. |
| `tests/test_guid_resolution.py` | Offline/online GUID resolution and caching. |
| `tests/test_m_comment_stripping.py` | M query comment handling. |
| `tests/test_main_report_layout.py` | Main lineage workbook layout. |
| `tests/test_model_change_impact_diff.py` | Model diff rules. |
| `tests/test_model_change_impact_excel_report.py` | Model impact workbook output. |
| `tests/test_model_change_impact_history_store.py` | Impact history persistence. |
| `tests/test_model_change_impact_impact.py` | Dependency and visual impact analysis. |
| `tests/test_model_change_impact_report_layout.py` | PBIX report layout extraction. |
| `tests/test_model_change_impact_requirement_mapping.py` | Requirement-to-visual mapping. |
| `tests/test_model_change_impact_requirement_report.py` | Requirement report output. |
| `tests/test_model_change_impact_requirements.py` | Requirements parsing/validation. |
| `tests/test_model_change_impact_snapshot.py` | Semantic snapshot extraction. |
| `tests/test_model_change_impact_visual_descriptions.py` | Visual descriptions and cache behavior. |
| `tests/test_updater.py` | Application update logic. |
| `tests/test_web_sources.py` | Web source/connector resolution. |
| `tools/check_manual_only_visual_impact.py` | Local diagnostic for manual-only visual impact. |
| `tools/inspect_resolution.py` | Local lineage resolution inspection. |
| `tools/show_actual_vs_candidate_diff.py` | Local comparison of actual report changes with candidate impacts. |
| `tools/populate_visual_cache.py` | Explicitly enriches visual-description cache using a named local PBIX. |

## Documentation and asset directories

| File or directory | Purpose |
|---|---|
| `.github/workflows/tests.yml` | Runs the Python test suite on Windows pull requests and main. |
| `docs/ARCHITECTURE.md` | Written workflow steps and component boundaries. |
| `docs/FILE_GUIDE.md` | This inventory. |
| `docs/RTM_BASELINE_MODEL_CHANGE_IMPACT_CONTEXT.md` | Detailed current RTM and model-impact contracts. |
| `docs/CODE_REVIEW.md` | Historical code-review notes. |
| `docs/EDGE_CASE_FIXES_REPORT.md` | Historical edge-case fixes. |
| `docs/V1_Weekly_Analysis_Report.md` | Historical version-one analysis. |
| `PowerBIDataflows/*.json` | Local exported dataflow definitions used by lineage resolution. |
| `previous_runs/` | Archived workbooks; may also contain the impact-history database. |
| `CygnusIN.Report/` | PBIP report definition, pages/visuals, and static resources. |
| `CygnusIN.SemanticModel/` | PBIP semantic model definition, queries, and layout. |
| `.venv/` | Local Python environment; reinstallable from requirements. |
| `.pytest_cache/`, `__pycache__/` | Reproducible local test/Python caches. |