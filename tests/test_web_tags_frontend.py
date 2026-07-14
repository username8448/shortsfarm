"""Frontend contracts for the legacy Tags view split."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from shortsfarm.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "shortsfarm" / "web" / "templates" / "index.html"
TAGS_PARTIAL = ROOT / "shortsfarm" / "web" / "templates" / "views" / "tags.html"
APP_JS = ROOT / "shortsfarm" / "web" / "static" / "app.js"
WEB_APP = ROOT / "shortsfarm" / "web" / "app.py"
TAGS_JS = ROOT / "shortsfarm" / "web" / "static" / "js" / "features" / "tags.js"

TAGS_IDS = [
    "v-tags",
    "tags-error",
    "tags-manager",
]

PUBLIC_TAG_HANDLERS = [
    "openGlobalTagsView",
    "createGlobalCatalogTag",
    "loadTagsView",
    "createCatalogTagFromManager",
    "onGlobalTagManagerSearchInput",
    "reloadCatalogTagsForUi",
    "renameCatalogTag",
    "recolorCatalogTag",
    "updateCatalogTagColor",
    "disableCatalogTag",
    "onTagManagerVideoSearchInput",
    "loadRandomTagManagerVideos",
    "toggleTagManagerVideoSelection",
    "assignTagToSelectedVideos",
    "removeTagFromSelectedVideos",
]

PRIVATE_TAG_STATE = [
    "tagManagerVideoResults",
    "selectedTagManagerVideoPaths",
    "tagManagerSearchQuery",
    "tagManagerSearchTimer",
    "tagManagerTagQuery",
]


def test_tags_view_is_jinja_partial() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    tags_html = TAGS_PARTIAL.read_text(encoding="utf-8")

    assert '{% include "views/tags.html" %}' in index_html
    assert 'id="v-tags"' not in index_html
    for element_id in TAGS_IDS:
        assert f'id="{element_id}"' in tags_html
    assert "Менеджер тегов" in tags_html
    assert "Создать тег" in tags_html


def test_rendered_index_contains_tags_partial_once_and_script_order() -> None:
    response = TestClient(create_app()).get("/")
    assert response.status_code == 200
    html = response.text

    for element_id in TAGS_IDS:
        assert html.count(f'id="{element_id}"') == 1

    files_script = "/static/js/features/files.js?v="
    tags_script = "/static/js/features/tags.js?v="
    app_script = "/static/app.js?v="
    assert files_script in html
    assert tags_script in html
    assert app_script in html
    assert html.index(files_script) < html.index(tags_script) < html.index(app_script)


def test_tags_js_boundary_public_handlers_and_private_state() -> None:
    tags_js = TAGS_JS.read_text(encoding="utf-8")
    app_js = APP_JS.read_text(encoding="utf-8")

    assert "window.ShortsFarmTags" in tags_js
    assert "configure(options = {})" in tags_js
    assert "syncCatalogVideoTags" in tags_js
    assert "DOMContentLoaded" not in tags_js
    assert "setInterval" not in tags_js
    assert re.search(r"^\s*(import|export)\s", tags_js, re.MULTILINE) is None
    assert re.search(r"\bconst\s+api\s*=", tags_js) is None
    assert re.search(r"\blet\s+api\s*=", tags_js) is None

    for handler in PUBLIC_TAG_HANDLERS:
        assert handler in tags_js
        assert f"function {handler}" not in app_js
        assert f"async function {handler}" not in app_js

    for state_name in PRIVATE_TAG_STATE:
        assert state_name not in app_js


def test_tags_bridge_contracts_are_wired_from_app_js() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    app_py = WEB_APP.read_text(encoding="utf-8")
    tags_js = TAGS_JS.read_text(encoding="utf-8")

    assert '"static" / "js" / "features" / "tags.js"' in app_py
    assert "window.ShortsFarmTags?.configure?.({" in app_js
    assert "getCurrentView: () => currentView" in app_js
    assert "getCatalogTags: () => catalogTags" in app_js
    assert "setCatalogTags: items =>" in app_js
    assert "loadCatalogTags," in app_js
    assert "window.ShortsFarmTags?.syncCatalogVideoTags?.(workspacePath, tags || [], updatedItem)" in app_js
    assert "if (id === 'tags') window.loadTagsView?.();" in app_js
    assert "window.ShortsFarmTags = {" in tags_js
    assert "state" not in tags_js.split("window.ShortsFarmTags = {", 1)[1].split("};", 1)[0]
