from lightrag.api.webui_branding import get_webui_branding


def test_get_webui_branding_reads_configured_brand_name(monkeypatch):
    monkeypatch.setenv("WEBUI_BRAND_NAME", "Tender KG")
    monkeypatch.setenv("WEBUI_TITLE", "Bid Graph")
    monkeypatch.setenv("WEBUI_DESCRIPTION", "Tender knowledge graph")

    branding = get_webui_branding()

    assert branding == {
        "webui_brand_name": "Tender KG",
        "webui_title": "Bid Graph",
        "webui_description": "Tender knowledge graph",
    }


def test_get_webui_branding_defaults_brand_name_to_lightrag(monkeypatch):
    monkeypatch.delenv("WEBUI_BRAND_NAME", raising=False)
    monkeypatch.delenv("WEBUI_TITLE", raising=False)
    monkeypatch.delenv("WEBUI_DESCRIPTION", raising=False)

    branding = get_webui_branding()

    assert branding == {
        "webui_brand_name": "LightRAG",
        "webui_title": None,
        "webui_description": None,
    }
