# Requirements Workbook Guide

Use the supplied [Requirements Template](../templates/Requirements_Template.xlsx) with the **Baseline Estimation** tab. The workbook tells the tool which report-level requirements should be mapped to which pages or visuals.

## Basic rules

- Keep the worksheet name exactly `Requirements`.
- Keep the header row in row 1.
- Give every requirement a unique ID.
- Fill in every mandatory field.
- Use Visual IDs, page names, or exact visual names when identifying scope.
- Do not enter model table, column, or measure names as scope. The tool derives model objects from the selected visuals and their dependencies.

## Columns

| Column | Required? | What to enter |
|---|---|---|
| Requirement ID | Yes | A unique business identifier, such as `REQ-104`. |
| Requirement Type | No | `New Requirement`, `Enhancement`, or `Bug Fix`. |
| Title | Yes | A short description of the requested change. |
| Status | Yes | `Proposed`, `Approved`, `In Progress`, `Done`, or `Cancelled`. |
| Description | Recommended | The expected behavior or business need. |
| Priority | Recommended | `Critical`, `High`, `Medium`, or `Low`. Defaults to `Medium`. |
| Raised Date | Recommended | Date in `YYYY-MM-DD` format. |
| Business Owner | No | Person accountable for the requirement. |
| Business Area | No | Finance, HR, Operations, or another business area. |
| Expected Change Date | No | Planned delivery date in `YYYY-MM-DD` format. |
| Impacted Pages | No | Exact report page names, comma-separated. |
| Impacted Visuals | No | Exact visual names, comma-separated. |
| Impacted Visual IDs | No | Exact Power BI Visual IDs, comma-separated. This is usually the most precise option. |
| Notes | No | Extra context, assumptions, or review comments. |

## Recommended way to scope a requirement

1. Run Baseline Estimation without requirements first.
2. Open the generated `RTM` sheet.
3. Find the relevant page, visual name, and Visual ID.
4. Copy the exact Visual ID into `Impacted Visual IDs`.
5. Add the business title, description, status, and priority.
6. Run Baseline Estimation again with the completed requirements workbook.
7. Review `Visual Impact by Requirement` and any review flags.

Visual IDs are preferred because names can be repeated or changed. Pages and visual names are also supported. Multiple values in a scope cell must be separated by commas.

## Example

| Requirement ID | Requirement Type | Title | Status | Priority | Raised Date | Impacted Visual IDs |
|---|---|---|---|---|---|---|
| REQ-104 | Enhancement | Add regional sales comparison | Approved | High | 2026-09-25 | visChartRegional |

The requirement will be mapped to the visual with ID `visChartRegional`. The tool then derives the measures, columns, tables, relationships, and dependent visuals associated with that visual.

## Status behavior

`Done` and `Cancelled` requirements are treated as inactive. Other valid statuses remain active for baseline analysis. When several requirements map to the same visual, the newest requirement is marked `Production` and older rows may be marked `Obsolete`.

## Common mistakes

- Naming the sheet `Requirement`, `RTM`, or another name instead of `Requirements`.
- Leaving `Requirement ID`, `Title`, or `Status` blank.
- Reusing the same Requirement ID.
- Using a display title where a Visual ID is available.
- Entering model object names instead of report-level scope.
- Using dates such as `25/09/2026` instead of `2026-09-25`.
- Expecting title or description keywords to infer impacted visuals. The tool deliberately does not use keyword inference.

Warnings are included when a requirement does not match a page, visual name, Visual ID, or supported visual tag. Review those warnings before relying on the RTM output.