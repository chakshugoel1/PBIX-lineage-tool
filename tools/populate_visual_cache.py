import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model_change_impact import report_layout, visual_descriptions

PBIX_PATH = ROOT / "DTS - CashPlus-Dashboard (1).pbix"
CACHE_PATH = ROOT / "visual_descriptions.json"

if not PBIX_PATH.exists():
    raise FileNotFoundError(f"PBIX not found: {PBIX_PATH}")

layout = report_layout.build_report_layout(str(PBIX_PATH))
result = visual_descriptions.enrich_descriptions_in_cache(layout, str(CACHE_PATH), force=True)
print(f"Wrote {len(result)} visual descriptions to {CACHE_PATH}")
for visual_id, description in list(result.items())[:5]:
    print(f"{visual_id}: {description[:160]}")
