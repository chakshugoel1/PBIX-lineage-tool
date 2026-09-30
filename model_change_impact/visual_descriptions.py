"""Cache-first visual descriptions for RTM reports."""
import hashlib
import json
import os
import re
from datetime import datetime, timezone


ENRICHMENT_VERSION = 2


def visual_context(visual):
    fields = []
    for field in visual.get("fields") or []:
        if field.get("table") and field.get("field"):
            fields.append({
                "kind": field.get("kind") or "",
                "table": field.get("table"),
                "field": field.get("field"),
                "role": field.get("role") or "",
            })
    return {
        "visual_type": visual.get("visual_type") or "",
        "display_name": visual.get("display_name") or "",
        "kpi_classification": visual.get("kpi_classification") or "",
        "fields": sorted(fields, key=lambda item: (
            item["kind"], item["table"], item["field"], item["role"])),
        "filters": visual.get("filters") or [],
    }


def visual_fingerprint(visual):
    payload = json.dumps(visual_context(visual), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def deterministic_description(visual):
    visual_type = (visual.get("visual_type") or "visual").casefold()
    names = [
        f"{field.get('table')}[{field.get('field')}]"
        for field in visual.get("fields") or []
        if field.get("table") and field.get("field")
    ]
    if "slicer" in visual_type or "textfilter" in visual_type:
        return f"Filters the report by {names[0] if names else 'the selected field'}."
    if any(token in visual_type for token in ("table", "matrix", "pivot")):
        return f"Displays {', '.join(names[:4]) or 'the configured fields'} in a table."
    if any(token in visual_type for token in ("card", "kpi", "gauge")):
        return f"Displays {names[0] if names else 'a key value'} as a KPI."
    if any(token in visual_type for token in ("chart", "column", "bar", "line", "area", "combo", "donut", "pie")):
        if len(names) >= 2:
            return f"Shows {names[0]} by {names[1]}."
        return f"Shows {names[0] if names else 'the configured values'} in a chart."
    if "button" in visual_type or "image" in visual_type:
        return "Provides report navigation or layout support."
    if "textbox" in visual_type:
        return "Displays configured report text."
    return f"Displays {', '.join(names[:4]) or 'configured report content'}."


def _format_field_label(field):
    table = field.get("table") or ""
    name = field.get("field") or ""
    if table and name:
        return f"{table}[{name}]"
    return name or "the selected field"


def _field_summary(visual, limit=None):
    parts = []
    seen = set()
    for field in visual.get("fields") or []:
        label = _format_field_label(field)
        if label in seen:
            continue
        seen.add(label)
        parts.append(label)
    if limit is not None and len(parts) > limit:
        remaining = len(parts) - limit
        parts = parts[:limit] + [f"{remaining} additional field{'s' if remaining != 1 else ''}"]
    return ", ".join(parts) if parts else "no data field was detected"


def _has_readable_title(value):
    return bool(value and not re.fullmatch(r"[0-9a-f]{12,}", str(value), re.IGNORECASE))


def _filters_summary(visual):
    filters = visual.get("filters") or []
    if not filters:
        return "No direct visual filter."
    values = []
    for item in filters:
        if item.get("fields"):
            values.extend(
                _format_field_label(field)
                for field in item.get("fields", [])
                if field.get("table") or field.get("field")
            )
        elif item.get("name"):
            values.append(str(item.get("name")))
    return "; ".join(sorted(set(values), key=str.casefold)) if values else "No direct visual filter."


def build_factual_description_context(visual):
    fields = []
    for field in visual.get("fields") or []:
        entry = {
            "kind": field.get("kind") or "",
            "table": field.get("table") or "",
            "field": field.get("field") or "",
            "role": field.get("role") or "",
        }
        if entry["table"] or entry["field"]:
            fields.append(entry)
    return {
        "visual_type": visual.get("visual_type") or "visual",
        "display_name": visual.get("display_name") or "",
        "kpi_classification": visual.get("kpi_classification") or "",
        "fields": sorted(fields, key=lambda item: (item["kind"], item["table"], item["field"], item["role"])),
        "filters": visual.get("filters") or [],
    }


def validate_ai_description(text, context):
    if not isinstance(text, str):
        return False
    cleaned = text.strip()
    if not cleaned:
        return False
    required_tokens = ["Purpose:", "Data:", "Grouping:", "Filters:", "Interaction:"]
    if not all(token in cleaned for token in required_tokens):
        return False

    banned = [
        "drives growth",
        "improves efficiency",
        "increases revenue",
        "boosts performance",
        "shows customer sentiment",
        "helps the business",
        "strategic",
        "critical for",
        "revenue trend",
        "performance trend",
        "trend",
        "overall performance",
    ]
    lowered = cleaned.casefold()
    if any(phrase in lowered for phrase in banned):
        return False

    visual_type = (context.get("visual_type") or "").casefold()
    fields = context.get("fields") or []
    labels = [
        _format_field_label(field)
        for field in fields if field.get("table") or field.get("field")
    ]
    if labels and not any(label in cleaned for label in labels):
        return False
    if "slicer" in visual_type and "Filters:" in cleaned and "Interaction:" in cleaned:
        return True
    return True


def ai_enrich_description(visual, provider=None):
    context = build_factual_description_context(visual)
    if provider is None:
        provider = _DefaultAIProvider()
    candidate = provider.generate_description(context)
    if not validate_ai_description(candidate, context):
        return deterministic_description(visual)
    return candidate.strip()


class _DefaultAIProvider:
    """Local, fact-bound description writer; no external AI service is used."""

    def generate_description(self, context):
        visual_type = (context.get("visual_type") or "visual").casefold()
        names = [
            _format_field_label(field)
            for field in context.get("fields") or []
            if field.get("table") or field.get("field")
        ]
        display_name = context.get("display_name") or ""
        title = display_name if _has_readable_title(display_name) else "This visual"
        filters = _filters_summary({"filters": context.get("filters") or []})
        data_scope = _field_summary({"fields": context.get("fields") or []}, limit=6)
        review_note = (
            " Review note: No readable title or data binding was detected, so its exact business purpose requires manual review."
            if not _has_readable_title(display_name) and not names else ""
        )
        if "slicer" in visual_type or "textfilter" in visual_type:
            target = names[0] if names else "the selected field"
            return (
                f"Purpose: Lets users narrow the report to selected {target} values.\n"
                f"Data: Uses {target} as the available selection field.\n"
                f"Grouping: Values are presented individually for selection.\n"
                f"Filters: {filters}\n"
                f"Interaction: Selections update the related report visuals."
            )
        if any(token in visual_type for token in ("card", "kpi", "gauge")):
            return (
                f"Purpose: Displays {title if _has_readable_title(display_name) else data_scope} as a single KPI value.\n"
                f"Data: The KPI is based on {data_scope}.\n"
                f"Grouping: No category grouping is configured; the visual presents one aggregated value.\n"
                f"Filters: {filters}\n"
                f"Interaction: The value responds to the active report and page filters.{review_note}"
            )
        if any(token in visual_type for token in ("table", "matrix", "pivot")):
            return (
                f"Purpose: {title} provides a detailed tabular view of the configured report data.\n"
                f"Data: It includes {data_scope}.\n"
                f"Grouping: Records are organised by the configured row and column fields.\n"
                f"Filters: {filters}\n"
                f"Interaction: The table responds to active report and page filters.{review_note}"
            )
        if any(token in visual_type for token in ("chart", "column", "bar", "line", "area", "combo", "donut", "pie")):
            primary = names[0] if names else "the configured field"
            second = names[1] if len(names) > 1 else None
            grouping = f"The visual groups results by {second}." if second else "No category grouping was detected."
            return (
                f"Purpose: {title} presents {primary} in a chart.\n"
                f"Data: The chart uses {data_scope}.\n"
                f"Grouping: {grouping}\n"
                f"Filters: {filters}\n"
                f"Interaction: The chart responds to active report and page filters.{review_note}"
            )
        return (
            f"Purpose: {title} is a supporting report element.\n"
            f"Data: Detected data scope: {data_scope}.\n"
            f"Grouping: No grouping information was detected.\n"
            f"Filters: {filters}\n"
            f"Interaction: The element may respond to the active report filters.{review_note}"
        )


def enrich_descriptions_in_cache(report_layout, cache_path, provider=None, force=False):
    cache = _load(cache_path)
    entries = cache.setdefault("visuals", {})
    changed = False
    now = datetime.now(timezone.utc).isoformat()
    for page in report_layout.get("pages", []):
        for visual in page.get("visuals", []):
            if visual.get("kind") != "visual" or not visual.get("visual_id"):
                continue
            visual_id = str(visual["visual_id"])
            fingerprint = visual_fingerprint(visual)
            entry = entries.get(visual_id)
            generated = ai_enrich_description(visual, provider=provider)
            if entry and entry.get("fingerprint") == fingerprint and entry.get("description"):
                if not force and entry.get("enrichment_version") == ENRICHMENT_VERSION:
                    continue
            if entry and entry.get("description"):
                entry.setdefault("history", []).append({
                    "fingerprint": entry.get("fingerprint"),
                    "description": entry.get("description"),
                    "generated_at": entry.get("generated_at"),
                    "generated_by": entry.get("generated_by"),
                })
            entries[visual_id] = {
                "fingerprint": fingerprint,
                "description": generated,
                "generated_by": "local_enriched",
                "enrichment_version": ENRICHMENT_VERSION,
                "generated_at": now,
                "history": entry.get("history", []) if entry else [],
            }
            changed = True
    if changed:
        _save(cache_path, cache)
    return {visual_id: entry["description"] for visual_id, entry in entries.items() if isinstance(entry, dict) and "description" in entry}


def load_or_generate(report_layout, cache_path):
    cache = _load(cache_path)
    entries = cache.setdefault("visuals", {})
    descriptions = {}
    changed = False
    now = datetime.now(timezone.utc).isoformat()
    for page in report_layout.get("pages", []):
        for visual in page.get("visuals", []):
            if visual.get("kind") != "visual" or not visual.get("visual_id"):
                continue
            visual_id = str(visual["visual_id"])
            fingerprint = visual_fingerprint(visual)
            entry = entries.get(visual_id)
            if entry and entry.get("fingerprint") == fingerprint and entry.get("description"):
                descriptions[visual_id] = entry["description"]
                continue
            description = deterministic_description(visual)
            if entry and entry.get("description"):
                entry.setdefault("history", []).append({
                    "fingerprint": entry.get("fingerprint"),
                    "description": entry.get("description"),
                    "generated_at": entry.get("generated_at"),
                })
            entries[visual_id] = {
                "fingerprint": fingerprint,
                "description": description,
                "generated_by": "deterministic",
                "generated_at": now,
                "history": entry.get("history", []) if entry else [],
            }
            descriptions[visual_id] = description
            changed = True
    if changed:
        _save(cache_path, cache)
    return descriptions


def _load(path):
    if not os.path.exists(path):
        return {"version": 1, "visuals": {}}
    try:
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) and isinstance(value.get("visuals"), dict) else {"version": 1, "visuals": {}}
    except (OSError, ValueError):
        return {"version": 1, "visuals": {}}


def _save(path, value):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=True)
    os.replace(temporary, path)
