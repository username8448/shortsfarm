"""Shared media API for the Universal Video Workbench."""
from __future__ import annotations

import mimetypes
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from .. import db
from ..ffmpeg_tools import probe_media_metadata
from ..services import VIDEO_EXTENSIONS
from ..workspace_fs import SYSTEM_FOLDERS, get_workspace_root, resolve_workspace_path


router = APIRouter()
ALLOWED_MEDIA_SECTIONS = set(SYSTEM_FOLDERS)
VIDEO_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
}


class VideoSegmentRequest(BaseModel):
    source_path: str
    label: str | None = None
    start_sec: float
    end_sec: float
    notes: str | None = None


class VideoSegmentUpdateRequest(BaseModel):
    label: str | None = None
    start_sec: float | None = None
    end_sec: float | None = None
    status: str | None = None
    notes: str | None = None


def _fail(exc: Exception, status_code: int = 400) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"message": str(exc) or exc.__class__.__name__},
    )


def _normalize_workspace_media_path(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("workspace path не указан.")
    if "\\" in text:
        raise PermissionError("Используйте '/' в workspace paths.")
    candidate = PurePosixPath(text)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise PermissionError("Небезопасный workspace path.")
    if not candidate.parts or candidate.parts[0] not in ALLOWED_MEDIA_SECTIONS:
        raise PermissionError("Видео можно открывать только из workspace media папок.")
    return candidate.as_posix()


def resolve_media_video_path(value: str) -> tuple[str, Path]:
    normalized = _normalize_workspace_media_path(value)
    path = resolve_workspace_path(normalized)
    if not path.exists():
        raise FileNotFoundError(f"Видео не найдено: {normalized}")
    if path.is_symlink():
        raise PermissionError("Symlink video запрещён.")
    if not path.is_file():
        raise ValueError("workspace path не является файлом.")
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("Файл не является поддерживаемым video.")
    return normalized, path


def _content_type(path: Path) -> str:
    return VIDEO_CONTENT_TYPES.get(
        path.suffix.lower(),
        mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    )


def _parse_byte_range(value: str, size: int) -> tuple[int, int]:
    text = str(value or "").strip()
    if not text.startswith("bytes=") or "," in text:
        raise ValueError("Некорректный HTTP Range.")
    spec = text[6:].strip()
    if "-" not in spec:
        raise ValueError("Некорректный HTTP Range.")
    start_text, end_text = spec.split("-", 1)
    if not start_text:
        try:
            suffix = int(end_text)
        except ValueError as exc:
            raise ValueError("Некорректный HTTP Range.") from exc
        if suffix <= 0:
            raise ValueError("Некорректный HTTP Range.")
        start = max(0, size - suffix)
        end = size - 1
    else:
        try:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
        except ValueError as exc:
            raise ValueError("Некорректный HTTP Range.") from exc
        if start < 0 or end < start:
            raise ValueError("Некорректный HTTP Range.")
        end = min(end, size - 1)
    if size <= 0 or start >= size:
        raise ValueError("HTTP Range находится вне файла.")
    return start, end


def _range_chunks(path: Path, start: int, length: int) -> Iterator[bytes]:
    remaining = length
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _media_response(path: Path, request: Request) -> Response:
    size = int(path.stat().st_size)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
    }
    range_header = request.headers.get("range")
    media_type = _content_type(path)
    if not range_header:
        return FileResponse(
            path,
            media_type=media_type,
            headers=headers,
            content_disposition_type="inline",
        )
    try:
        start, end = _parse_byte_range(range_header, size)
    except ValueError:
        return Response(
            status_code=416,
            headers={**headers, "Content-Range": f"bytes */{size}"},
        )
    length = end - start + 1
    return StreamingResponse(
        _range_chunks(path, start, length),
        status_code=206,
        media_type=media_type,
        headers={
            **headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(length),
        },
    )


def _metadata_payload(normalized: str, path: Path) -> dict[str, Any]:
    stat = path.stat()
    metadata = probe_media_metadata(path)
    return {
        "path": normalized,
        "filename": path.name,
        "size_bytes": int(stat.st_size),
        **metadata,
    }


def _segment_payload(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _validate_segment_range(
    *,
    source_path: str,
    start_sec: float,
    end_sec: float,
) -> tuple[str, Path, float]:
    normalized, path = resolve_media_video_path(source_path)
    start = float(start_sec)
    end = float(end_sec)
    if start < 0:
        raise ValueError("start_sec должен быть >= 0.")
    if end <= start:
        raise ValueError("end_sec должен быть больше start_sec.")
    metadata = probe_media_metadata(path)
    duration = metadata.get("duration_sec")
    if duration is not None and end > float(duration) + 0.001:
        raise ValueError("Segment выходит за длительность source video.")
    return normalized, path, end - start


@router.get("/video")
def media_video(path: str, request: Request) -> Response:
    try:
        db.init_db()
        _normalized, video_path = resolve_media_video_path(path)
        return _media_response(video_path, request)
    except PermissionError as exc:
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/metadata")
def media_metadata(path: str) -> dict[str, Any]:
    try:
        db.init_db()
        normalized, video_path = resolve_media_video_path(path)
        return _metadata_payload(normalized, video_path)
    except PermissionError as exc:
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.get("/videos")
def media_videos() -> dict[str, Any]:
    try:
        db.init_db()
        root = get_workspace_root()
        if root is None:
            raise ValueError("workspace_root не настроен.")
        sections: list[dict[str, Any]] = []
        for section in SYSTEM_FOLDERS:
            folder = root / section
            items: list[dict[str, Any]] = []
            if folder.is_dir() and not folder.is_symlink():
                for candidate in sorted(folder.rglob("*")):
                    if (
                        candidate.is_symlink()
                        or not candidate.is_file()
                        or candidate.suffix.lower() not in VIDEO_EXTENSIONS
                    ):
                        continue
                    relative = candidate.relative_to(root).as_posix()
                    items.append({
                        "path": relative,
                        "filename": candidate.name,
                        "size_bytes": int(candidate.stat().st_size),
                    })
            sections.append({"key": section, "items": items})
        return {"sections": sections}
    except PermissionError as exc:
        raise _fail(exc, 403)
    except Exception as exc:
        raise _fail(exc)


@router.get("/segments")
def media_segments(path: str) -> dict[str, Any]:
    try:
        db.init_db()
        normalized, _video_path = resolve_media_video_path(path)
        return {
            "items": [
                _segment_payload(row)
                for row in db.list_video_segments_for_source(normalized)
            ]
        }
    except PermissionError as exc:
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.post("/segments")
def media_segment_create(req: VideoSegmentRequest) -> dict[str, Any]:
    try:
        db.init_db()
        normalized, _path, duration = _validate_segment_range(
            source_path=req.source_path,
            start_sec=req.start_sec,
            end_sec=req.end_sec,
        )
        segment_id = db.create_video_segment(
            source_path=normalized,
            label=req.label,
            start_sec=req.start_sec,
            end_sec=req.end_sec,
            duration_sec=duration,
            notes=req.notes,
        )
        row = db.get_video_segment(segment_id)
        assert row is not None
        return {"item": _segment_payload(row)}
    except PermissionError as exc:
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.patch("/segments/{segment_id}")
def media_segment_update(segment_id: int, req: VideoSegmentUpdateRequest) -> dict[str, Any]:
    try:
        db.init_db()
        row = db.get_video_segment(segment_id)
        if row is None:
            raise FileNotFoundError("Segment не найден.")
        start = float(req.start_sec if req.start_sec is not None else row["start_sec"])
        end = float(req.end_sec if req.end_sec is not None else row["end_sec"])
        _normalized, _path, duration = _validate_segment_range(
            source_path=str(row["source_path"]),
            start_sec=start,
            end_sec=end,
        )
        db.update_video_segment(
            segment_id,
            label=req.label,
            start_sec=start,
            end_sec=end,
            duration_sec=duration,
            status=req.status,
            notes=req.notes,
        )
        updated = db.get_video_segment(segment_id)
        assert updated is not None
        return {"item": _segment_payload(updated)}
    except PermissionError as exc:
        raise _fail(exc, 403)
    except FileNotFoundError as exc:
        raise _fail(exc, 404)
    except Exception as exc:
        raise _fail(exc)


@router.delete("/segments/{segment_id}")
def media_segment_delete(segment_id: int) -> dict[str, Any]:
    db.init_db()
    deleted = db.delete_video_segment(segment_id)
    if not deleted:
        raise _fail(FileNotFoundError("Segment не найден."), 404)
    return {"deleted": True}
