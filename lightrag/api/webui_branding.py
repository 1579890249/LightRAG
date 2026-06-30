import os


DEFAULT_WEBUI_BRAND_NAME = "LightRAG"


def _clean_optional_env(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def get_webui_branding() -> dict[str, str | None]:
    """Return WebUI branding values exposed through server status endpoints."""
    return {
        "webui_brand_name": _clean_optional_env(
            os.getenv("WEBUI_BRAND_NAME")
        )
        or DEFAULT_WEBUI_BRAND_NAME,
        "webui_title": _clean_optional_env(os.getenv("WEBUI_TITLE")),
        "webui_description": _clean_optional_env(os.getenv("WEBUI_DESCRIPTION")),
    }
