# PBIX Lineage Tool Architecture

This is a Windows desktop application with optional script entry points. It has four workflows: source lineage, Power BI dataflow export, baseline estimation, and model change impact. They share the GUI but not a single analysis engine. See [the file guide](FILE_GUIDE.md) for module ownership and [the RTM context](RTM_BASELINE_MODEL_CHANGE_IMPACT_CONTEXT.md) for workbook contracts and limitations.

## Application boundaries

```text
User
	-> app.py
		 -> configures Windows identity and runtime logging
		 -> gui/main_window.py
				-> validates selected paths and options
				-> creates the matching QThread worker
				-> receives progress, success summary, or failure signal
				-> displays counts and output paths

gui/worker.py
	+-> PipelineWorker ---------------------> source lineage workbooks
	+-> DataflowExportWorker ---------------> exported dataflow JSON
	+-> BaselineEstimationWorker -----------> baseline RTM workbook
	+-> ModelChangeImpactWorker ------------> model-change-impact workbook
	+-> UpdateWorker / UpdateCheckWorker ---> application update status

Root compatibility scripts
	-> import implementations from reporting/, services/, core/, or cli/
	-> preserve older commands and imports without duplicating the engines
```

`gui/worker.py` keeps long operations off the UI thread. Workers derive output names, optionally archive existing outputs, check cancellation between expensive stages, convert exceptions into user-facing failure signals, and return compact result counts. `config.py` supplies defaults for script use; GUI selections override those defaults.

## Source lineage and dataflow export

**Dataflow export**

```text
Workspace ID + output folder
  -> DataflowExportWorker
	  -> archive existing JSON files when enabled
	  -> services/dataflow_export.py
		  -> start powershell/Export-AllDataflows.ps1
		  -> Power BI interactive authentication
		  -> list all dataflows in the workspace
		  -> export each definition as JSON
		  -> stream progress lines to the GUI
		  -> parse the final ##RESULT## JSON message
		  -> persist workspace/dataflow GUID-to-name mappings
	  -> success: return exported file list
	  -> failure/timeout: stop the subprocess and return a readable error
```

**Source lineage**

```text
PBIX + dataflow JSON folder + optional online GUID lookup
  -> PipelineWorker
	  -> derive Generated_<report>_Lineage.xlsx
	  -> derive Dataflow_Table_Lineage_Report_<report>.xlsx
	  -> archive existing outputs when enabled
	  -> reporting/lineage_report.build_report
		  -> load PBIX model, Power Query M, relationships, DAX, and report usage
		  -> parse every provided dataflow JSON into entity/query indexes
		  -> load known GUID mappings from guid_dataflow_names.json
		  -> resolve unknown GUIDs from exported metadata/entity fingerprints
		  -> optionally ask Power BI for unresolved GUID names
		  -> classify whether each semantic-model table is used
		  -> for every table:
			  -> no M query? classify as calculated/static/no-query
			  -> Table.Combine of independent sources? classify as union/review
			  -> otherwise follow local M query references to a dataflow entity
			  -> follow linked/reference entities through deeper dataflows
			  -> resolve the terminal physical connector
				  -> Oracle schema/table
				  -> SharePoint/Excel/CSV file
				  -> Web URL or other recognized connector
			  -> flag missing, ambiguous, inaccessible, or multi-source cases
			  -> assign found / unresolved / union / no-query status
	  -> reporting/lineage_report.write_workbook
		  -> main lineage sheet
		  -> dataflows used
		  -> unused tables
		  -> dataflow file coverage
		  -> transformations sheet
	  -> reporting/dataflow_table_report.build_and_save
		  -> overview sheet
		  -> one-row-per-table lineage sheet
	  -> return status counts and manual-review rows to the GUI
```

The lineage engine follows report tables through M queries and referenced dataflows to physical connectors. Unknown or ambiguous sources are reported for manual review. Dataflow export is a separate input-acquisition workflow; it does not change the lineage algorithm. `previous_runs/` stores archived output copies when archiving is enabled.

## Baseline estimation and change impact

**Baseline estimation (one PBIX)**

```text
One PBIX + output path + optional requirements workbook
  -> BaselineEstimationWorker
	  -> snapshot.build_snapshot
		  -> use pbixray to extract tables, columns, measures, and relationships
		  -> enrich metadata where available (expressions, formats, hidden flags,
			  descriptions, lineage tags, KPI IDs, and modified timestamps)
	  -> report_layout.build_report_layout
		  -> detect PBIR or legacy Report/Layout format
		  -> extract pages, page filters, visuals, visual filters, and field bindings
		  -> exclude visual groups from real visual rows
		  -> classify native/custom KPI-like visuals
		  -> assign stable fallback IDs to legacy visuals without IDs
	  -> optional requirements processing
		  -> validate the Requirements sheet and normalized fields
		  -> match scope by generated requirement number, visual ID, visual name,
			  page name, or title tag
		  -> do not infer scope from title/description keywords
		  -> seed model objects from the matched visuals' field bindings
		  -> expand objects through the DAX dependency engine
		  -> retain declared visuals; filter noisy dependency-only slicer results
		  -> warn on unmatched scope or one visual assigned to many requirements
	  -> baseline_estimation.build_report
		  -> represent all selected/current objects as hypothetical modifications
		  -> reuse impact.analyze_impact for object-to-visual tracing
		  -> build one baseline row per real visual
		  -> fingerprint each visual and reuse or create its cached description
		  -> generate/reuse requirement numbers
		  -> mark the latest visual requirement Production and older rows Obsolete
		  -> write RTM
		  -> write Requirement Copy
		  -> write Visual Impact by Requirement
	  -> atomically replace the output workbook
	  -> return object, requirement, visual, page, and KPI counts
```

**Model change impact (two PBIX files)**

```text
Baseline PBIX + changed PBIX + output path
  -> ModelChangeImpactWorker
	  -> build baseline semantic snapshot
	  -> build changed semantic snapshot
	  -> extract baseline report layout
	  -> extract changed report layout
	  -> diff.diff_snapshots
		  -> flatten tables, columns, measures, and relationships by object type
		  -> match tables/columns/measures by stable lineage tag first
		  -> fall back to identity keys when tags are absent
		  -> identify rename candidates when a lineage-tag match changed identity
		  -> compare relevant metadata fields
		  -> classify added / removed / changed / unchanged
		  -> classify relationships as manual, auto-detected, or uncertain
	  -> impact.analyze_impact
		  -> build DAX references for measures and calculated columns
		  -> reverse the graph from dependency to dependent
		  -> seed the graph with each changed object
		  -> broaden table changes to that table's columns
		  -> broaden relationship changes to columns on both related tables
		  -> walk upward to all dependent model objects
		  -> index changed-report visuals by bound measure/column
		  -> classify matching visuals as direct or dependency-chain impact
		  -> detect dangling bindings to removed objects
	  -> compare baseline and changed report layouts
		  -> detect added/removed visuals, changed visual types, and changed bindings
	  -> excel_report.build_excel_report
		  -> Summary and Impact Summary
		  -> Changed Tables, Measures, Columns, and Relationships
		  -> rich-text before/after values for changed fields
	  -> return change counts and unique impacted-visual count
```

The snapshot is model metadata; the layout is report-page, visual, filter, and binding metadata. Baseline estimation uses one PBIX to build visual-grain planning rows. Model change impact compares two snapshots, propagates changed objects through DAX dependents to bound visuals, and compares report layouts separately. Broad table/relationship impacts need manual review. Neither path renders report visuals or compiles DAX.

## Local assets and ownership

- `CygnusIN.pbip`, `CygnusIN.Report/`, and `CygnusIN.SemanticModel/` form a local editable Power BI project. They are not application source or disposable build output.
- PBIX files, exported dataflows, reference workbooks, GUID mappings, visual-description caches, and archived workbooks may contain client information. They are ignored by Git by default; preserve or remove them only after a data-retention decision.
- `.venv/`, Python bytecode, and `.pytest_cache/` are reproducible local environment/cache files. `Install.cmd`, `bootstrap-install.ps1`, and `setup.ps1` provision the application and should remain in source control.

## Verification

From the repository root, use `.venv\Scripts\python.exe -m pytest` for automated checks. The offline worker-to-output suite is `tests/test_e2e_workflows.py`: it exercises lineage, export, baseline estimation (with and without requirements), and model change impact using synthetic inputs. PBIX extraction and the Power BI subprocess are replaced at their external boundaries. CI runs the full pytest suite on Windows with Python 3.12.

These checks do not validate live PBIX extraction, Power BI sign-in, or GUI rendering. Perform those smoke checks locally with authorized data before a release; never commit the client inputs or generated output.

### E2E test design

The E2E suite calls each real `gui/worker.py` orchestration path synchronously. It connects to the worker's Qt success and failure signals, writes into pytest's temporary directory, reopens generated files, and verifies their externally visible contract. Only boundaries that require client files or external services are replaced.

| Test | Replaced boundary | Real code exercised | Main assertions |
|---|---|---|---|
| `test_baseline_worker_writes_rtm_workbook` | PBIX semantic and report-layout extraction return a synthetic Sales model and one card visual. | Baseline worker, impact tracing, description cache, workbook writer, atomic file replacement. | No failure signal; three expected sheets; one RTM visual row; cache file created. |
| `test_baseline_worker_maps_requirements` | Same PBIX extraction replacement; the test creates a real temporary requirements workbook. | Requirements loader/validator, visual-ID mapping, dependency expansion, baseline report writer. | One active requirement; copied requirement ID; mapped card appears in visual-impact output. |
| `test_model_change_worker_writes_impact_workbook` | PBIX extraction returns baseline/changed synthetic snapshots; the changed measure expression is deliberately different. | Worker staging, model diff, DAX impact analysis, report-layout comparison, Excel report writer. | One changed measure; one impacted visual; six expected sheets; direct card impact row. |
| `test_lineage_worker_writes_both_workbooks` | The expensive PBIX/dataflow loading stage returns one synthetic no-query table. | Pipeline worker naming and summary logic, main lineage writer, transformations output, companion report builder. | No failure signal; both workbooks exist; main status label and companion calculated row are correct. |
| `test_export_worker_receives_offline_powershell_result` | `subprocess.Popen` is replaced by a fake process that writes one JSON file and emits the real `##RESULT##` protocol. | Export worker, export service result parser, progress/result handling, output-file contract. | No failure signal; returned file list matches; exported JSON is readable and correct. |

The tests use no repository PBIX, PBIP, dataflow export, cached description, credentials, or network connection. `tmp_path` isolates every generated workbook, JSON file, and cache; pytest removes them after the run.

**Coverage boundary:** these are workflow integration tests rather than fully rendered desktop automation. They prove worker-to-engine-to-file behavior, but they do not click GUI controls, authenticate to Power BI, execute the real PowerShell exporter, parse a real PBIX through pbixray, open files in Excel/Power BI Desktop, or verify screenshots. Those operations remain release smoke tests because they require Windows desktop state, credentials, or authorized report data.

## Publishing safely

1. Fetch and compare the intended destination branch with the local branch; review commits already ahead of the upstream as well as uncommitted work. Do not assume the configured upstream is the desired release target.
2. Inspect every proposed file for client data or credentials, including tests, documentation, and Git history. `.gitignore` protects only untracked files, not material already committed.
3. Run the full test suite and `git diff --check`; stage only reviewed source, tests, workflow, and documentation paths, then review `git diff --cached --stat` and `git diff --cached`.
4. After explicit approval, push to a feature branch and open a pull request with passing CI. Do not push straight to `main`.