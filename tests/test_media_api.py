from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from starlette.requests import Request


def _request(range_header: str | None = None) -> Request:
    headers = []
    if range_header:
        headers.append((b"range", range_header.encode("ascii")))
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/media/video",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    })


def _workspace(tmp_path: Path) -> Path:
    from shortsfarm.workspace_fs import set_workspace_root

    return set_workspace_root(tmp_path / "workspace")


def _metadata(_path: Path) -> dict:
    return {
        "duration_sec": 45.2,
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "video_codec": "h264",
        "audio_codec": "aac",
        "has_audio": True,
        "container": "mp4",
    }


def test_media_path_rejects_absolute_and_traversal(tmp_path):
    from shortsfarm.web import media_api

    _workspace(tmp_path)

    with pytest.raises(HTTPException) as absolute:
        media_api.media_metadata("/tmp/video.mp4")
    assert absolute.value.status_code == 403

    with pytest.raises(HTTPException) as traversal:
        media_api.media_metadata("../video.mp4")
    assert traversal.value.status_code == 403


def test_metadata_endpoint_returns_expected_fields(tmp_path, monkeypatch):
    from shortsfarm.web import media_api

    root = _workspace(tmp_path)
    video = root / "sources" / "example.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(media_api, "probe_media_metadata", _metadata)

    payload = media_api.media_metadata("sources/example.mp4")

    assert payload["path"] == "sources/example.mp4"
    assert payload["filename"] == "example.mp4"
    assert payload["size_bytes"] == 5
    assert payload["duration_sec"] == 45.2
    assert payload["width"] == 1080
    assert payload["height"] == 1920
    assert payload["fps"] == 30
    assert payload["video_codec"] == "h264"
    assert payload["audio_codec"] == "aac"
    assert payload["has_audio"] is True
    assert payload["container"] == "mp4"


def test_video_endpoint_supports_range_request(tmp_path):
    from shortsfarm.web import media_api

    root = _workspace(tmp_path)
    video = root / "cuts" / "range.mp4"
    video.write_bytes(b"0123456789")

    full = media_api.media_video("cuts/range.mp4", _request())
    partial = media_api.media_video(
        "cuts/range.mp4",
        _request(range_header="bytes=3-6"),
    )

    assert isinstance(full, FileResponse)
    assert full.headers["accept-ranges"] == "bytes"
    assert isinstance(partial, StreamingResponse)
    assert partial.status_code == 206
    assert partial.headers["content-range"] == "bytes 3-6/10"

    async def consume() -> bytes:
        return b"".join([chunk async for chunk in partial.body_iterator])

    assert asyncio.run(consume()) == b"3456"


def test_create_list_and_delete_video_segment(tmp_path, monkeypatch):
    from shortsfarm.web import media_api

    root = _workspace(tmp_path)
    video = root / "prepared" / "segment.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(media_api, "probe_media_metadata", _metadata)

    with pytest.raises(HTTPException) as invalid:
        media_api.media_segment_create(
            media_api.VideoSegmentRequest(
                source_path="prepared/segment.mp4",
                label="bad",
                start_sec=10,
                end_sec=5,
            )
        )
    assert invalid.value.status_code == 400

    created = media_api.media_segment_create(
        media_api.VideoSegmentRequest(
            source_path="prepared/segment.mp4",
            label="segment 1",
            start_sec=12.5,
            end_sec=42.5,
            notes="note",
        )
    )["item"]
    listed = media_api.media_segments("prepared/segment.mp4")["items"]

    assert created["duration_sec"] == 30.0
    assert listed[0]["id"] == created["id"]
    assert listed[0]["label"] == "segment 1"

    deleted = media_api.media_segment_delete(created["id"])
    assert deleted == {"deleted": True}
    assert media_api.media_segments("prepared/segment.mp4")["items"] == []
