from __future__ import annotations

from fastapi.routing import APIRoute

from shortsfarm.web.app import create_app


INTEGRATIONS_ROUTE_CONTRACT = {
    ("GET", "/api/settings/youtube"): "youtube_settings",
    ("POST", "/api/settings/youtube"): "youtube_settings_save",
    (
        "POST",
        "/api/settings/youtube/import-client-json",
    ): "youtube_settings_import_client_json",
    ("POST", "/api/settings/youtube/clear"): "youtube_settings_clear",
    ("GET", "/api/publish/youtube/oauth-profiles"): "youtube_oauth_profiles",
    ("POST", "/api/publish/youtube/oauth-profiles"): "youtube_oauth_profiles_create",
    (
        "POST",
        "/api/publish/youtube/oauth-profiles/import-client-json",
    ): "youtube_oauth_profiles_import",
    (
        "PATCH",
        "/api/publish/youtube/oauth-profiles/{profile_id}",
    ): "youtube_oauth_profiles_update",
    (
        "DELETE",
        "/api/publish/youtube/oauth-profiles/{profile_id}",
    ): "youtube_oauth_profiles_delete",
    (
        "POST",
        "/api/publish/youtube/oauth-profiles/{profile_id}/set-default",
    ): "youtube_oauth_profiles_set_default",
    ("GET", "/api/publish/youtube/accounts"): "youtube_accounts",
    (
        "PATCH",
        "/api/publish/youtube/accounts/{account_id}",
    ): "youtube_account_update",
    ("POST", "/api/publish/youtube/accounts/sync-metadata"): "youtube_accounts_sync_metadata",
    (
        "POST",
        "/api/publish/youtube/accounts/{account_id}/sync-metadata",
    ): "youtube_account_sync_metadata",
    (
        "POST",
        "/api/publish/youtube/accounts/{account_id}/disconnect",
    ): "youtube_disconnect",
    ("POST", "/api/publish/youtube/connect/start"): "youtube_connect_start",
    ("GET", "/api/publish/youtube/oauth/callback"): "youtube_oauth_callback",
}


GENERIC_PUBLISHING_ROUTES = {
    ("POST", "/api/publish/youtube/clips/{clip_id}/upload"),
    ("POST", "/api/publish/youtube/clips/{clip_id}/enqueue"),
    ("GET", "/api/publish/jobs"),
    ("POST", "/api/publish/jobs/{job_id}/run"),
    ("POST", "/api/publish/worker/run-once"),
    ("GET", "/api/publish/schedule-groups"),
    ("POST", "/api/publish/schedule-groups"),
    ("POST", "/api/workspace/clips/youtube/enqueue"),
}

EXPECTED_INTEGRATIONS_MODULE = "shortsfarm.web.integrations_api"


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


def test_integrations_route_contract_is_stable() -> None:
    routes = _route_map()

    assert len(INTEGRATIONS_ROUTE_CONTRACT) == 17
    for key, endpoint_name in INTEGRATIONS_ROUTE_CONTRACT.items():
        route = routes[key]
        assert route.path == key[1]
        assert route.methods == {key[0]}
        assert route.name == endpoint_name
        assert route.include_in_schema is True
        assert route.endpoint.__module__ == EXPECTED_INTEGRATIONS_MODULE


def test_generic_publishing_routes_are_not_owned_by_integrations() -> None:
    routes = _route_map()

    for key in GENERIC_PUBLISHING_ROUTES:
        assert key in routes
        assert routes[key].endpoint.__module__ != EXPECTED_INTEGRATIONS_MODULE


def test_app_has_no_duplicate_method_path_routes_after_integrations_split() -> None:
    seen: set[tuple[str, str]] = set()
    duplicates: list[tuple[str, str]] = []

    for method, path, _route in _route_entries():
        key = (method, path)
        if key in seen:
            duplicates.append(key)
        seen.add(key)

    assert duplicates == []
