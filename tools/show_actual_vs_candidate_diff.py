"""
show_actual_vs_candidate_diff.py

Compare PBIX files and show the clear breakdown of:
- Actual report changes (from report layout metadata)
- Candidate impact (from dependency heuristics)
- Projection-only rows (candidates without actual change)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_change_impact import snapshot, report_layout, diff, impact

baseline_path = r"C:\Users\cgoel\Downloads\V1 Weekly.pbix"
changed_path = r"C:\Users\cgoel\Downloads\V1_Weekly_UAT_TA_Back_Table (1).pbix"

print("=" * 80)
print("ACTUAL vs CANDIDATE IMPACT COMPARISON")
print("=" * 80)

baseline_snapshot = snapshot.build_snapshot(baseline_path)
changed_snapshot = snapshot.build_snapshot(changed_path)
baseline_layout = report_layout.build_report_layout(baseline_path)
changed_layout = report_layout.build_report_layout(changed_path)

full_diff = diff.diff_snapshots(baseline_snapshot, changed_snapshot)

# Get actual report changes
actual_report_changes = report_layout.compare_report_layouts(baseline_layout, changed_layout)
print(f"\n📊 ACTUAL REPORT CHANGES (from layout metadata):")
print(f"   Total: {len(actual_report_changes)} visual layout changes detected")
if actual_report_changes:
    for row in actual_report_changes[:5]:
        print(f"   - {row['name']} on page {row['page']}: {row['details']}")
    if len(actual_report_changes) > 5:
        print(f"   ... and {len(actual_report_changes) - 5} more")

# Filter relationships to manual-only
manual_only_diff = dict(full_diff)
manual_only_diff["relationships"] = {
    "added": [r for r in full_diff["relationships"].get("added", []) if r.get("detection_method") == "MANUAL"],
    "removed": [r for r in full_diff["relationships"].get("removed", []) if r.get("detection_method") == "MANUAL"],
    "changed": [r for r in full_diff["relationships"].get("changed", []) if r.get("detection_method") == "MANUAL"],
    "unchanged_count": full_diff["relationships"].get("unchanged_count", 0),
}

# Get candidate impact
impact_result = impact.analyze_impact(baseline_snapshot, changed_snapshot, manual_only_diff, changed_layout)

candidate_visual_count = {
    (v["page_id"], v["visual_id"])
    for section in impact_result.values()
    for row in section
    for v in row.get("impacted_visuals", [])
}

print(f"\n🎯 CANDIDATE VISUAL IMPACT (from dependency heuristics):")
print(f"   Total: {len(candidate_visual_count)} unique visuals in impact paths")
print(f"   - Measures: {len(impact_result['measures'])} impact paths")
print(f"   - Columns: {len(impact_result['columns'])} impact paths")
print(f"   - Relationships (MANUAL only): {len(impact_result['relationships'])} impact paths")

# Compare: which visuals are ACTUAL vs CANDIDATE only
actual_visual_ids = {(row['page'], row['name']) for row in actual_report_changes}
candidate_visual_ids = {(v["page_id"], v["visual_id"]) for section in impact_result.values() for row in section for v in row.get("impacted_visuals", [])}

both = actual_visual_ids & candidate_visual_ids
actual_only = actual_visual_ids - candidate_visual_ids
candidate_only = candidate_visual_ids - actual_visual_ids

print(f"\n📈 BREAKDOWN:")
print(f"   Actual ONLY (layout changed, no heuristic path): {len(actual_only)}")
print(f"   Candidate ONLY (heuristic path, layout unchanged): {len(candidate_only)}")
print(f"   BOTH Actual & Candidate (real + heuristic confirm): {len(both)}")
print(f"   ")
print(f"   Total Unique Visuals (Actual OR Candidate): {len(actual_visual_ids | candidate_visual_ids)}")

print(f"\n💡 HOW TO READ THE WORKBOOK:")
print(f"   ✅ Actual Report Change = YES, Candidate = YES")
print(f"      → Most trustworthy: real change confirmed by both metrics")
print(f"   ⚠️  Actual Report Change = NO, Candidate = YES")
print(f"      → Heuristic guess (e.g., relationship fan-out) without proof of visual change")
print(f"   ℹ️  Actual Report Change = YES, Candidate = NO")
print(f"      → Layout changed but heuristic missed the dependency path")

print("\n" + "=" * 80)
