"""Baseline route contracts before splitting the legacy web API."""
from __future__ import annotations

from fastapi.routing import APIRoute

from shortsfarm.web.app import create_app


FILES_ROUTE_CONTRACT = {
    ("GET", "/api/settings/workspace"): "workspace_settings_get",
    ("POST", "/api/settings/workspace"): "workspace_settings_save",
    (
        "POST",
        "/api/settings/workspace/pick-directory",
    ): "workspace_settings_pick_directory",
    ("GET", "/api/files"): "files_list",
    ("POST", "/api/files/folder"): "files_folder_create",
    ("PATCH", "/api/files/rename"): "files_rename",
    ("POST", "/api/files/move"): "files_move",
    ("DELETE", "/api/files"): "files_delete",
    ("POST", "/api/files/import-source"): "files_import_source",
    ("POST", "/api/files/register-source"): "files_register_source",
    ("GET", "/api/fs/roots"): "fs_roots",
    ("GET", "/api/fs/list"): "fs_list",
    ("GET", "/api/fs/video-info"): "fs_video_info",
    ("GET", "/api/fs/thumbnail"): "fs_thumbnail",
    ("POST", "/api/fs/open-mpv"): "fs_open_mpv",
}

ADJACENT_NON_FILES_ROUTES = {
    ("POST", "/api/settings/database/reset"): "settings_database_reset",
    ("POST", "/api/local-dialogs/pick"): "local_dialog_pick",
}


def _route_entries() -> list[tuple[str, str, str, bool, str]]:
    app = create_app()
    entries: list[tuple[str, str, str, bool, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = sorted(route.methods - {"HEAD", "OPTIONS"})
        for method in methods:
            entries.append(
                (
                    method,
                    route.path,
                    route.name,
                    bool(route.include_in_schema),
                    route.endpoint.__module__,
                )
            )
    return entries


def _route_map() -> dict[tuple[str, str], tuple[str, bool, str]]:
    return {
        (method, path): (endpoint_name, include_in_schema, module)
        for method, path, endpoint_name, include_in_schema, module in _route_entries()
    }


def test_files_route_contract_is_stable():
    routes = _route_map()

    for key, expected_endpoint in FILES_ROUTE_CONTRACT.items():
        assert key in routes
        endpoint_name, include_in_schema, module = routes[key]
        assert endpoint_name == expected_endpoint
        assert include_in_schema is True
        assert module == "shortsfarm.web.files_api"


def test_adjacent_routes_exist_but_are_not_part_of_files_contract():
    routes = _route_map()

    for key, expected_endpoint in ADJACENT_NON_FILES_ROUTES.items():
        assert key in routes
        endpoint_name, include_in_schema, module = routes[key]
        assert endpoint_name == expected_endpoint
        assert include_in_schema is True
        assert key not in FILES_ROUTE_CONTRACT
        assert module != "shortsfarm.web.files_api"


def test_app_has_no_duplicate_method_path_routes():
    seen: set[tuple[str, str]] = set()
    duplicates: list[tuple[str, str]] = []

    for method, path, _endpoint_name, _include_in_schema, _module in _route_entries():
        key = (method, path)
        if key in seen:
            duplicates.append(key)
        seen.add(key)

    assert duplicates == []
