# Code-generated architecture diagrams

These diagrams describe the architecture evidenced by the repository as inspected on 2026-09-30. They are Graphviz DOT sources, rendered to SVG and PNG by `render_architecture.py`.

## Diagram index

- [System overview SVG](system_overview.svg) | [PNG preview](system_overview.png) | [DOT source](system_overview.dot)
- [Lineage subsystem SVG](lineage_subsystem.svg) | [PNG preview](lineage_subsystem.png) | [DOT source](lineage_subsystem.dot)
- [Model impact subsystems SVG](model_impact_subsystems.svg) | [PNG preview](model_impact_subsystems.png) | [DOT source](model_impact_subsystems.dot)
- [User journeys SVG](user_journeys.svg) | [PNG preview](user_journeys.png) | [DOT source](user_journeys.dot)

## Reproduce

From the repository root:

```powershell
.venv\Scripts\python.exe docs\architecture\render_architecture.py
```

The renderer requires Graphviz `dot` on `PATH`. It renders every `*.dot` file in this directory to same-name `.svg` and `.png` files.

For this inspection, native `dot` was unavailable, so the same DOT sources were rendered with Graphviz 16.1.0 through the temporary `@viz-js/viz` WASM package and converted to PNG with `sharp`:

```powershell
$tools = Join-Path $env:TEMP "pbix-architecture-viz"
npm install --prefix $tools @viz-js/viz sharp
$env:VIZ_PKG = (Resolve-Path (Join-Path $tools "node_modules\@viz-js\viz\dist\viz.cjs")).Path
$env:SHARP_PKG = (Resolve-Path (Join-Path $tools "node_modules\sharp\dist\index.cjs")).Path
node docs\architecture\render_architecture_viz.mjs
```

## Evidence mapping

| Diagram component | Evidence in source |
|---|---|
| Desktop entry point and runtime boundary | `app.py`; `gui/main_window.py:1053`; `requirements.txt` |
| Five UI tabs and local path validation | `gui/main_window.py` (`MainWindow`, `HomeInterface`, `DataflowExportInterface`, `ModelChangeImpactInterface`, `BaselineEstimationInterface`, `AboutInterface`) |
| Background execution and Qt signals | `gui/worker.py` (`PipelineWorker`, `DataflowExportWorker`, `ModelChangeImpactWorker`, `BaselineEstimationWorker`, update workers) |
| Source lineage and workbook outputs | `reporting/lineage_report.py`; `core/lineage_lib.py`; `reporting/dataflow_table_report.py`; `reporting/transformations_report.py` |
| Dataflow export and optional GUID lookup | `services/dataflow_export.py`; `services/guid_resolver.py`; `powershell/Export-AllDataflows.ps1`; `powershell/Resolve-DataflowNames.ps1` |
| Power BI authentication and REST calls | PowerShell scripts: `Connect-PowerBIServiceAccount`, `Get-PowerBIWorkspace`, `Invoke-PowerBIRestMethod` |
| Model snapshots, layouts, diff, impact, and Excel report | `model_change_impact/snapshot.py`; `report_layout.py`; `diff.py`; `impact.py`; `excel_report.py` |
| Baseline requirements and visual traceability | `model_change_impact/baseline_estimation.py`; `requirements.py`; `requirement_mapping.py`; `requirement_report.py`; `visual_descriptions.py` |
| SQLite history | `model_change_impact/history_store.py` (`impact_history.db` schema and `record_run`) |
| Settings and update deployment | `gui/settings.py`; `gui/updater.py`; `Install.cmd`; `bootstrap-install.ps1`; `setup.ps1` |
| Compatibility script entry points | `build_lineage_report.py`; `build_dataflow_table_lineage_report.py`; `build_transformations_report.py`; `compare_final_source_table.py`; `cli/legacy_cli.py` |

## Coverage checklist

- [x] Repository structure and documentation conventions inspected.
- [x] No `AGENTS.md` found; no additional repository instructions apply.
- [x] Desktop entry point, QApplication, Windows identity, logging, and restart behavior inspected.
- [x] UI tabs, controls, input validation, worker startup, result handling, and error InfoBars inspected.
- [x] V1 lineage engine, dataflow JSON inputs, GUID cache, physical-source resolution, and workbook outputs inspected.
- [x] Dataflow export PowerShell boundary, authentication, REST listing/download, result protocol, timeout, and mapping persistence inspected.
- [x] Optional online GUID-name lookup inspected.
- [x] V2 semantic snapshot, PBIR/legacy layout parsing, diff, DAX impact propagation, visual comparison, and Excel output inspected.
- [x] Baseline estimation, optional requirements workbook, visual descriptions, RTM outputs, and SQLite history inspected.
- [x] Configuration, per-user settings, archive helpers, update mechanism, installer, dependency pins, tests, and local PBIP assets inspected.
- [x] No backend HTTP server, route table, hosted database, message queue, scheduled job, or application-level authentication implementation found.
- [x] Planned/incomplete behavior is represented only where source evidence describes it as optional, best-effort, heuristic, legacy, or unverified.

## Gaps and assumptions

- The repository contains no deployed infrastructure definition beyond the Windows installer/bootstrap scripts. Azure, cloud hosting, CI deployment, and a hosted API are therefore not diagrammed.
- Power BI access is verified only as delegated PowerShell behavior. Token storage and the exact Microsoft authentication UI are owned by `MicrosoftPowerBIMgmt` and are not inspectable in this repository.
- Real PBIX parsing and live Power BI calls are external boundaries. The test suite replaces those boundaries in offline tests; the diagrams label those calls as implemented but do not claim live-service validation.
- `Report/Layout` parsing is explicitly best-effort in `model_change_impact/report_layout.py`; the legacy path and custom-visual KPI classification should be treated as lower-confidence behavior.
- Root compatibility modules and legacy CLI/comparison scripts are shown as compatibility surfaces, not as separate engines or web routes.
- “Asynchronous” in these diagrams means background Qt thread or subprocess execution. No durable event bus or scheduled background job was found.