from model_change_impact import visual_descriptions


class FakeAIProvider:
    def __init__(self, text):
        self.text = text

    def generate_description(self, context):
        return self.text


def _visual(field="Total Sales", visual_type="card"):
    return {
        "kind": "visual",
        "visual_id": "v1",
        "visual_type": visual_type,
        "display_name": "Sales KPI",
        "kpi_classification": "certain",
        "fields": [{"kind": "measure", "table": "Sales", "field": field, "role": "Values"}],
        "filters": [],
    }


def test_description_is_generated_and_cached(tmp_path):
    path = tmp_path / "visual_descriptions.json"
    layout = {"pages": [{"page_id": "p1", "display_name": "Overview", "visuals": [_visual()]}]}

    descriptions = visual_descriptions.load_or_generate(layout, str(path))

    assert descriptions["v1"] == "Displays Sales[Total Sales] as a KPI."
    assert path.exists()


def test_unchanged_visual_reuses_cached_description(tmp_path):
    path = tmp_path / "visual_descriptions.json"
    layout = {"pages": [{"page_id": "p1", "display_name": "Overview", "visuals": [_visual()]}]}
    visual_descriptions.load_or_generate(layout, str(path))
    cache = visual_descriptions._load(str(path))
    cache["visuals"]["v1"]["description"] = "Approved business wording."
    visual_descriptions._save(str(path), cache)

    descriptions = visual_descriptions.load_or_generate(layout, str(path))

    assert descriptions["v1"] == "Approved business wording."


def test_changed_visual_keeps_previous_description_in_history(tmp_path):
    path = tmp_path / "visual_descriptions.json"
    first = {"pages": [{"page_id": "p1", "display_name": "Overview", "visuals": [_visual()]}]}
    second_visual = _visual(field="Total Sales YTD")
    second = {"pages": [{"page_id": "p1", "display_name": "Overview", "visuals": [second_visual]}]}

    visual_descriptions.load_or_generate(first, str(path))
    visual_descriptions.load_or_generate(second, str(path))
    cache = visual_descriptions._load(str(path))

    assert "Sales[Total Sales YTD]" in cache["visuals"]["v1"]["description"]
    assert "Sales[Total Sales]" in cache["visuals"]["v1"]["history"][0]["description"]


def test_ai_enrichment_writes_factual_description_to_cache(tmp_path):
    path = tmp_path / "visual_descriptions.json"
    layout = {"pages": [{"page_id": "p1", "display_name": "Overview", "visuals": [_visual()]}]}
    provider = FakeAIProvider(
        "Purpose: Displays Sales[Total Sales] as a KPI.\n"
        "Data: Measure Sales[Total Sales].\n"
        "Grouping: None.\n"
        "Filters: No direct visual filter.\n"
        "Interaction: KPI card; responds to report filters."
    )

    visual_descriptions.enrich_descriptions_in_cache(layout, str(path), provider=provider)
    cache = visual_descriptions._load(str(path))

    assert cache["visuals"]["v1"]["generated_by"] == "local_enriched"
    assert cache["visuals"]["v1"]["enrichment_version"] == 2
    assert cache["visuals"]["v1"]["description"].startswith("Purpose:")
    assert "Sales[Total Sales]" in cache["visuals"]["v1"]["description"]


def test_ai_validation_rejects_invented_business_claims():
    context = {"visual_type": "card", "fields": [{"table": "Sales", "field": "Total Sales"}]}
    assert not visual_descriptions.validate_ai_description(
        "Purpose: Shows the revenue trend. Data: Measure Sales[Total Sales]. Grouping: None. Filters: None. Interaction: KPI.",
        context,
    )
    assert visual_descriptions.validate_ai_description(
        "Purpose: Displays Sales[Total Sales] as a KPI.\nData: Measure Sales[Total Sales].\nGrouping: None.\nFilters: No direct visual filter.\nInteraction: KPI card; responds to report filters.",
        context,
    )
