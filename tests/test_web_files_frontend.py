"""Frontend contracts for the legacy Files view split."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from shortsfarm.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "shortsfarm" / "web" / "templates" / "index.html"
FILES_PARTIAL = ROOT / "shortsfarm" / "web" / "templates" / "views" / "files.html"
APP_JS = ROOT / "shortsfarm" / "web" / "static" / "app.js"
WEB_APP = ROOT / "shortsfarm" / "web" / "app.py"
FILES_JS = ROOT / "shortsfarm" / "web" / "static" / "js" / "features" / "files.js"

FILES_IDS = [
    "v-files",
    "files-setup",
    "files-manager",
    "files-root-path",
    "files-sidebar",
    "files-breadcrumbs",
    "files-error",
    "files-list",
]

PUBLIC_FILES_HANDLERS = [
    "loadManagedFiles",
    "refreshManagedFiles",
    "managedFilesUp",
    "createManagedFolder",
    "renameManagedItem",
    "moveManagedItem",
    "deleteManagedItem",
    "importManagedSource",
    "registerManagedSource",
    "openManagedFileInQueue",
]

PRIVATE_FILES_HELPERS = [
    "renderManagedFiles",
    "managedAbsolutePath",
    "workspaceRelativeFromAbsolute",
]


def test_files_view_is_jinja_partial():
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    files_html = FILES_PARTIAL.read_text(encoding="utf-8")

    assert '{% include "views/files.html" %}' in index_html
    assert 'id="v-files"' not in index_html
    for element_id in FILES_IDS:
        assert f'id="{element_id}"' in files_html


def test_rendered_index_contains_files_partial_once_and_script_order():
    response = TestClient(create_app()).get("/")
    assert response.status_code == 200
    html = response.text

    for element_id in FILES_IDS:
        assert html.count(f'id="{element_id}"') == 1

    files_script = '/static/js/features/files.js?v='
    app_script = '/static/app.js?v='
    assert files_script in html
    assert app_script in html
    assert html.index(files_script) < html.index(app_script)


def test_files_js_boundary_and_public_handlers():
    files_js = FILES_JS.read_text(encoding="utf-8")
    app_js = APP_JS.read_text(encoding="utf-8")

    assert "window.ShortsFarmFiles" in files_js
    assert "getWorkspaceRoot" in files_js
    assert "setWorkspaceRoot" in files_js
    for handler in PUBLIC_FILES_HANDLERS:
        assert handler in files_js
        assert f"function {handler}" not in app_js
        assert f"async function {handler}" not in app_js

    for helper in PRIVATE_FILES_HELPERS:
        assert helper in files_js
        assert helper not in app_js

    assert "managedFilesState" not in app_js
    assert re.search(r"^\s*(import|export)\s", files_js, re.MULTILINE) is None
    assert re.search(r"\bconst\s+api\s*=", files_js) is None
    assert re.search(r"\blet\s+api\s*=", files_js) is None
    assert "DOMContentLoaded" not in files_js
    assert "setInterval" not in files_js


def test_files_asset_version_and_cross_feature_bridge_contracts():
    app_py = WEB_APP.read_text(encoding="utf-8")
    app_js = APP_JS.read_text(encoding="utf-8")
    files_js = FILES_JS.read_text(encoding="utf-8")

    assert '"static" / "js" / "features" / "files.js"' in app_py
    assert "window.ShortsFarmFiles?.getWorkspaceRoot?.()" in app_js
    assert "window.ShortsFarmFiles?.setWorkspaceRoot?.(root)" in app_js
    assert "window.ShortsFarmFiles?.setWorkspaceRoot?.(data.workspace_root" in app_js
    assert "window.ShortsFarmFiles = {" in files_js
    assert "state" not in files_js.split("window.ShortsFarmFiles = {", 1)[1].split("};", 1)[0]
