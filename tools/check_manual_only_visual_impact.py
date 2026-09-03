import copy
import os

from model_change_impact import snapshot, report_layout, diff, impact

baseline_path = r"C:\Users\cgoel\Downloads\V1 Weekly.pbix"
changed_path = r"C:\Users\cgoel\Downloads\V1_Weekly_UAT_TA_Back_Table (1).pbix"

baseline_snapshot = snapshot.build_snapshot(baseline_path)
changed_snapshot = snapshot.build_snapshot(changed_path)
changed_layout = report_layout.build_report_layout(changed_path)
full_diff = diff.diff_snapshots(baseline_snapshot, changed_snapshot)

relationship_counts = {}
for section_name in ("added", "removed", "changed"):
    items = full_diff["relationships"].get(section_name, [])
    c = {"MANUAL": 0, "AUTO_DETECTED": 0, "UNCERTAIN": 0, "UNKNOWN": 0}
    for item in items:
        kind = item.get("detection_method", "UNKNOWN")
        c[kind] = c.get(kind, 0) + 1
    relationship_counts[section_name] = c

print("Relationship detection counts by section:")
for section_name, counts in relationship_counts.items():
    print(section_name, counts)

manual_only_diff = copy.deepcopy(full_diff)
manual_only_diff["relationships"] = {
    "added": [r for r in full_diff["relationships"].get("added", []) if r.get("detection_method") == "MANUAL"],
    "removed": [r for r in full_diff["relationships"].get("removed", []) if r.get("detection_method") == "MANUAL"],
    "changed": [r for r in full_diff["relationships"].get("changed", []) if r.get("detection_method") == "MANUAL"],
    "unchanged_count": full_diff["relationships"].get("unchanged_count", 0),
}

impact_result = impact.analyze_impact(baseline_snapshot, changed_snapshot, manual_only_diff, changed_layout)
unique_visuals = {
    (v["page_id"], v["visual_id"])
    for section in impact_result.values()
    for row in section
    for v in row.get("impacted_visuals", [])
}

print(f"Manual-only relationship diff count: {sum(len(v) for v in manual_only_diff['relationships'].values() if isinstance(v,list))}")
print(f"Unique impacted visuals with manual-only relationships: {len(unique_visuals)}")
print("Sections:")
for section_name, rows in impact_result.items():
    count = len(rows)
    visuals = sum(len(row.get("impacted_visuals", [])) for row in rows)
    print(f"  {section_name}: {count} rows, {visuals} visual references")
