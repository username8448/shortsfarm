from __future__ import annotations

from fastapi.routing import APIRoute

from shortsfarm.web.app import create_app


TAG_ROUTE_CONTRACT = {
    ("GET", "/api/tags"): "tags_list",
    ("POST", "/api/tags"): "tag_create",
    ("PATCH", "/api/tags/{tag_id}"): "tag_update",
    ("DELETE", "/api/tags/{tag_id}"): "tag_disable",
    ("GET", "/api/catalog/videos/search"): "catalog_videos_search",
    ("GET", "/api/catalog/videos/random"): "catalog_videos_random",
    ("GET", "/api/catalog/videos/tags"): "catalog_video_tags",
    ("POST", "/api/catalog/videos/tags"): "catalog_video_tags_update",
}

ADJACENT_NON_TAG_ROUTES = {
    ("GET", "/api/storage-profiles/{profile_id}/tag-rules"): "local_storage_profile_tag_rules",
    ("PATCH", "/api/storage-profiles/{profile_id}/tag-rules"): "local_storage_profile_tag_rules_update",
    ("GET", "/api/workspace/clips/{item_key}"): "workspace_clip_detail",
    ("PATCH", "/api/workspace/clips/{item_key}"): "workspace_clip_update",
}

EXPECTED_TAG_MODULE = "shortsfarm.web.tags_api"
FUTURE_TAG_MODULE = "shortsfarm.web.tags_api"


def _route_map() -> dict[tuple[str, str], APIRoute]:
    routes: dict[tuple[str, str], APIRoute] = {}
    for route in create_app().routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if method in {"HEAD", "OPTIONS"}:
                continue
            routes[(method, route.path)] = route
    return routes


def test_tag_route_contract_is_stable() -> None:
    routes = _route_map()

    for key, endpoint_name in TAG_ROUTE_CONTRACT.items():
        route = routes[key]
        assert route.path == key[1]
        assert route.methods == {key[0]}
        assert route.name == endpoint_name
        assert route.include_in_schema is True
        assert route.endpoint.__module__ == EXPECTED_TAG_MODULE


def test_adjacent_routes_exist_but_are_not_part_of_tags_contract() -> None:
    routes = _route_map()

    for key, endpoint_name in ADJACENT_NON_TAG_ROUTES.items():
        route = routes[key]
        assert key not in TAG_ROUTE_CONTRACT
        assert route.name == endpoint_name
        assert route.include_in_schema is True
        assert route.endpoint.__module__ != FUTURE_TAG_MODULE


def test_app_has_no_duplicate_method_path_routes() -> None:
    seen: set[tuple[str, str]] = set()
    duplicates: list[tuple[str, str]] = []
    for route in create_app().routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if method in {"HEAD", "OPTIONS"}:
                continue
            key = (method, route.path)
            if key in seen:
                duplicates.append(key)
            seen.add(key)

    assert duplicates == []
