from __future__ import annotations

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from shortsfarm.web.app import create_app


STORAGE_PROFILE_CORE_ROUTE_CONTRACT = {
    ("GET", "/api/storage-profiles/ready-videos"): "local_storage_profile_ready_videos",
    (
        "POST",
        "/api/storage-profiles/{profile_id}/auto-import/run",
    ): "local_storage_profile_auto_import_run",
    ("GET", "/api/storage-profiles"): "local_storage_profiles",
    ("POST", "/api/storage-profiles"): "local_storage_profile_create",
    ("GET", "/api/storage-profiles/{profile_id}"): "local_storage_profile_detail",
    ("PATCH", "/api/storage-profiles/{profile_id}"): "local_storage_profile_update",
    ("DELETE", "/api/storage-profiles/{profile_id}"): "local_storage_profile_disable",
    ("GET", "/api/storage-profiles/{profile_id}/tag-rules"): "local_storage_profile_tag_rules",
    (
        "PATCH",
        "/api/storage-profiles/{profile_id}/tag-rules",
    ): "local_storage_profile_tag_rules_update",
    (
        "POST",
        "/api/storage-profiles/{profile_id}/tag-sync/run",
    ): "local_storage_profile_tag_sync_run",
    ("GET", "/api/storage-profiles/{profile_id}/items"): "local_storage_profile_items",
    ("POST", "/api/storage-profiles/{profile_id}/items"): "local_storage_profile_item_add",
    (
        "DELETE",
        "/api/storage-profiles/{profile_id}/items/{item_id}",
    ): "local_storage_profile_item_remove",
}


ADJACENT_STORAGE_PROFILE_YOUTUBE_ROUTES = {
    (
        "POST",
        "/api/storage-profiles/{profile_id}/youtube/link",
    ): "local_storage_profile_youtube_link",
    (
        "DELETE",
        "/api/storage-profiles/{profile_id}/youtube/link",
    ): "local_storage_profile_youtube_unlink",
    (
        "POST",
        "/api/storage-profiles/{profile_id}/youtube/sync-branding",
    ): "local_storage_profile_youtube_sync_branding",
    (
        "GET",
        "/api/storage-profiles/{profile_id}/publish-settings",
    ): "local_storage_profile_publish_settings",
    (
        "PATCH",
        "/api/storage-profiles/{profile_id}/publish-settings",
    ): "local_storage_profile_publish_settings_update",
    (
        "GET",
        "/api/storage-profiles/{profile_id}/publish-jobs",
    ): "local_storage_profile_publish_jobs",
    (
        "GET",
        "/api/storage-profiles/{profile_id}/youtube/videos",
    ): "local_storage_profile_youtube_videos",
    (
        "POST",
        "/api/storage-profiles/{profile_id}/youtube/enqueue",
    ): "local_storage_profile_youtube_enqueue",
    (
        "POST",
        "/api/storage-profiles/{profile_id}/youtube/sync",
    ): "local_storage_profile_youtube_sync",
}


EXPECTED_CORE_MODULE = "shortsfarm.web.storage_profiles_api"
EXPECTED_YOUTUBE_MODULE = "shortsfarm.web.storage_profile_youtube_api"


def _route_entries() -> list[tuple[str, str, APIRoute]]:
    entries: list[tuple[str, str, APIRoute]] = []
    for route in create_app().routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or set()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            entries.append((method, route.path, route))
    return entries


def _route_map() -> dict[tuple[str, str], APIRoute]:
    return {(method, path): route for method, path, route in _route_entries()}


def test_storage_profile_core_route_contract_is_stable() -> None:
    routes = _route_map()

    for key, endpoint_name in STORAGE_PROFILE_CORE_ROUTE_CONTRACT.items():
        route = routes[key]
        assert route.path == key[1]
        assert route.methods == {key[0]}
        assert route.name == endpoint_name
        assert route.include_in_schema is True
        assert route.endpoint.__module__ == EXPECTED_CORE_MODULE


def test_storage_profile_youtube_routes_remain_adjacent_to_core_contract() -> None:
    routes = _route_map()

    for key, endpoint_name in ADJACENT_STORAGE_PROFILE_YOUTUBE_ROUTES.items():
        route = routes[key]
        assert key not in STORAGE_PROFILE_CORE_ROUTE_CONTRACT
        assert route.path == key[1]
        assert route.methods == {key[0]}
        assert route.name == endpoint_name
        assert route.include_in_schema is True
        assert route.endpoint.__module__ == EXPECTED_YOUTUBE_MODULE


def test_app_has_no_duplicate_method_path_routes() -> None:
    seen: set[tuple[str, str]] = set()
    duplicates: list[tuple[str, str]] = []

    for method, path, _route in _route_entries():
        key = (method, path)
        if key in seen:
            duplicates.append(key)
        seen.add(key)

    assert duplicates == []


def test_storage_profile_ready_videos_route_precedes_profile_detail_route() -> None:
    route_keys = [
        (method, path)
        for method, path, _route in _route_entries()
        if (method, path)
        in {
            ("GET", "/api/storage-profiles/ready-videos"),
            ("GET", "/api/storage-profiles/{profile_id}"),
        }
    ]

    assert route_keys.index(("GET", "/api/storage-profiles/ready-videos")) < route_keys.index(
        ("GET", "/api/storage-profiles/{profile_id}")
    )


def test_storage_profile_ready_videos_is_not_routed_as_profile_id(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("SHORTSFARM_HOME", str(tmp_path / "shortsfarm-home"))

    with TestClient(create_app()) as client:
        response = client.get("/api/storage-profiles/ready-videos")

    assert response.status_code != 422
    assert response.request.url.path == "/api/storage-profiles/ready-videos"
