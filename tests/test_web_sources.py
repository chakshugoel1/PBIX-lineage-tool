from core import lineage_lib as ll
from reporting import lineage_report


def test_json_wrapped_servicenow_web_contents_uses_web_source():
    m_code = '''let
        request = Web.Contents(
            "https://soprasteriacorpstg.service-now.com",
            [RelativePath = "/api/now/table/sc_task"]
        ),
        response = Json.Document(request)
    in
        response'''

    connector = ll.detect_connector(m_code)
    details = ll.extract_physical_details(connector, m_code, ll.Universe({}))
    final_source, folder, file_name = lineage_report.format_final_source(details)

    assert connector == "Web Contents"
    assert details["source_system"] == "ServiceNow"
    assert details["host"] == "https://soprasteriacorpstg.service-now.com"
    assert details["relative_path"] == "/api/now/table/sc_task"
    assert details["resource"] == "sc_task"
    assert details["parser"] == "Json Document"
    assert "System = ServiceNow" in final_source
    assert "Endpoint = /api/now/table/sc_task" in final_source
    assert folder == "https://soprasteriacorpstg.service-now.com"
    assert file_name == "sc_task"


def test_generic_web_contents_full_url_is_rest_api():
    m_code = 'let Source = Web.Contents("https://api.example.com/v1/orders") in Source'

    details = ll.extract_physical_details(ll.detect_connector(m_code), m_code, ll.Universe({}))

    assert details["connector"] == "Web Contents"
    assert details["source_system"] == "REST API"
    assert details["endpoint"] == "https://api.example.com/v1/orders"
    assert details["resource"] == "orders"


def test_json_without_transport_remains_json_document():
    m_code = 'let Source = Json.Document(Binary.FromText("e30=", BinaryEncoding.Base64)) in Source'

    assert ll.detect_connector(m_code) == "Json Document"
