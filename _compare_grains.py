"""Throwaway analysis: compare requirement-mapping accuracy across the three
scope grains (visual names vs visual IDs vs both) against the actual impacted
visuals from the CygnusIN -> cygnusprev diff. Deleted after use."""
from model_change_impact import report_layout, requirement_mapping, snapshot

ACTUAL = {
    ("HeadcountTodayvalue", "CXO"), ("HeadcountTodayvalue", "DU Head"),
    ("HeadcountPrevYearvalue", "CXO"), ("HeadcountPrevYearvalue", "DU Head"),
    ("Your Team Member Details", "Access"), ("Your Team Member Details", "CXO"),
    ("Your Team Member Details", "DU Head"),
}

IDS = [
    "d2b320c53532dfecc211", "526ecdf0470b81de04a4", "6897a41505887576b092",
    "a79d578d88906a8fe4c7", "50db87983a8b434a61b4", "ed1b0710b0e270375b49",
    "fc73acfd17540ec4a628",
]
NAMES = "Your Team Member Details, HeadcountPrevYearvalue, HeadcountTodayvalue"


def _req(visuals="", visual_ids=""):
    return {
        "id": "CR-1", "title": "t", "status": "Approved", "active": True,
        "description": "", "priority": "High", "raised_date": "",
        "business_owner": "", "business_area": "", "expected_change_date": "",
        "impacted_pages": "", "impacted_visuals": visuals,
        "impacted_visual_ids": visual_ids, "notes": "", "row_number": 2,
    }


def evaluate(label, snap, layout, req):
    mapping = requirement_mapping.build_requirement_mapping(snap, layout, [req])
    entry = mapping["CR-1"]
    mapped = {(info.get("visual_display_name") or "", info.get("page_display_name") or "")
              for info in entry["visuals"].values()}
    # Visual-level precision/recall vs the actual 7.
    hits = mapped & ACTUAL
    extra = mapped - ACTUAL
    missing = ACTUAL - mapped
    # Also: how many visuals are DIRECT (declared) vs dependency-expanded.
    direct = {k for k, v in entry["visuals"].items()
              if v["source"] in ("Visual ID", "Visual Name", "Page", "Tag")}
    direct_named = {(entry["visuals"][k].get("visual_display_name") or "",
                     entry["visuals"][k].get("page_display_name") or "") for k in direct}
    print(f"=== {label} ===")
    print(f"  mapped (visual,page) total: {len(mapped)}")
    print(f"  direct-declared visuals: {len(direct_named)}")
    print(f"  actual 7 present: {len(hits)}/7   missing: {sorted(missing)}")
    print(f"  extra beyond actual: {len(extra)}")
    print(f"  objects seeded/derived: {len(entry['objects'])}")
    print(f"  precision vs actual: {len(hits)}/{len(mapped)} = {len(hits)/max(len(mapped),1):.1%}")
    print()
    return entry


print("Building snapshot + layout for CygnusIN.pbix (once)...")
snap = snapshot.build_snapshot("CygnusIN.pbix")
layout = report_layout.build_report_layout("CygnusIN.pbix")

# Do the 7 IDs resolve to the expected (visual, page) pairs?
from model_change_impact.requirement_mapping import _visual_index
vindex = _visual_index(layout)
print("--- ID -> (visual, page) resolution check ---")
for vid in IDS:
    key = next((k for k, v in vindex.items() if str(v["visual_id"]) == vid), None)
    if key:
        v = vindex[key]
        print(f"  {vid}: {v['visual_display_name']!r} on {v['page_display_name']!r}")
    else:
        print(f"  {vid}: NOT FOUND")
print()

evaluate("A. Visual NAMES only", snap, layout, _req(visuals=NAMES))
evaluate("B. Visual IDs only (the 7)", snap, layout, _req(visual_ids=", ".join(IDS)))
evaluate("C. Names + IDs", snap, layout, _req(visuals=NAMES, visual_ids=", ".join(IDS)))
