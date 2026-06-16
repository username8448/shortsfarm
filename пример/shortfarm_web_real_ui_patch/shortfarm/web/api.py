from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from .. import db
from ..config import data_dir, db_path, ensure_dirs, input_dir, logs_dir, output_dir
from ..ffmpeg_tools import require_binary
from ..render import render_queued, retry_failed_clips
from ..services import FileSplitResult, FolderSplitItem, split_video_file, split_video_folder
from .schemas import RenderRequest, RetryFailedRequest, SplitRequest

router = APIRouter()


def _init() -> None:
    ensure_dirs()
    db.init_db()


def _fail(exc: Exception, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"message": str(exc)})


def _status_value(value: str | None) -> str:
    return value or "inbox"


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    sec = total % 60
    return f"{hours}:{minutes:02d}:{sec:02d}" if hours else f"{minutes}:{sec:02d}"


def _row(row: Any, key: str, default: Any = None) -> Any:
    try:
        if key in row.keys():
            return row[key]
    except Exception:
        pass
    try:
        return row[key]
    except Exception:
        return default


def _segment_dict(index: int, start: float, end: float, path: str | None = None) -> dict[str, Any]:
    return {
        "index": index,
        "start_sec": float(start),
        "end_sec": float(end),
        "duration_sec": float(end - start),
        "path": path,
    }


def _split_result(result: FileSplitResult) -> dict[str, Any]:
    return {
        "status": "preview" if result.dry_run else "done",
        "dry_run": result.dry_run,
        "source_path": str(result.source_path),
        "video_id": result.video_id,
        "job_id": result.job_id,
        "duration_sec": result.duration_sec,
        "duration_text": _format_duration(result.duration_sec),
        "output_dir": str(result.output_dir),
        "segments_count": len(result.segment_ranges),
        "segments": [
            _segment_dict(i, start, end, str(result.files[i - 1]) if i - 1 < len(result.files) else None)
            for i, (start, end) in enumerate(result.segment_ranges, start=1)
        ],
        "files": [str(path) for path in result.files],
    }


def _folder_item(item: FolderSplitItem) -> dict[str, Any]:
    if item.error:
        return {"path": str(item.source_path), "status": "failed", "error": item.error}
    assert item.result is not None
    data = _split_result(item.result)
    return {"path": str(item.source_path), "status": "ok", "error": None, "result": data}


def _job_dict(row: Any) -> dict[str, Any]:
    video_id = _row(row, "video_id")
    segment_count = db.count_segments(int(video_id)) if video_id is not None else 0
    status = str(_row(row, "status", ""))
    if status == "done":
        progress = 100
    elif status == "failed":
        progress = 100
    elif status == "running":
        progress = 50
    else:
        progress = 0
    return {
        "id": int(row["id"]),
        "video_id": video_id,
        "type": _row(row, "type", "split"),
        "status": status,
        "mode": _row(row, "mode", ""),
        "segment_seconds": _row(row, "segment_seconds"),
        "progress": progress,
        "done_items": segment_count,
        "total_items": segment_count if status in {"done", "failed"} else None,
        "current_file": _row(row, "video_title", "") or "",
        "error": _row(row, "error", "") or "",
        "created_at": _row(row, "created_at"),
        "started_at": _row(row, "started_at"),
        "finished_at": _row(row, "finished_at"),
    }


def _video_dict(row: Any) -> dict[str, Any]:
    video_id = int(row["id"])
    return {
        "id": video_id,
        "title": row["title"],
        "source_path": row["source_path"],
        "duration_sec": row["duration_sec"],
        "duration_text": _format_duration(row["duration_sec"]),
        "review_status": _status_value(_row(row, "review_status")),
        "mark_count": int(_row(row, "mark_count", db.count_marks(video_id))),
        "clip_count": int(_row(row, "clip_count", db.count_clips(video_id))),
    }


def _clip_dict(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "video_id": int(row["video_id"]),
        "video_title": _row(row, "video_title", ""),
        "mark_id": _row(row, "mark_id"),
        "status": _row(row, "status", ""),
        "cut_mode": _row(row, "cut_mode", ""),
        "output_path": _row(row, "output_path", "") or "",
        "temp_path": _row(row, "temp_path", "") or "",
        "error": _row(row, "error", "") or "",
    }


@router.get("/status")
def status() -> dict[str, Any]:
    try:
        _init()
        videos = [_video_dict(row) for row in db.list_videos_with_counts()]
        jobs = [_job_dict(row) for row in db.list_jobs(limit=20)]
        clip_counts = db.count_clips_by_status()
        job_counts = db.count_jobs_by_status()
        video_counts = db.count_videos_by_review_status()
        errors = [
            {
                "kind": row["kind"], "id": row["id"], "video_id": row["video_id"],
                "status": row["status"], "error": row["error"], "at": row["at"],
            }
            for row in db.list_recent_errors(limit=5)
        ]
        return {
            "videos_total": db.count_videos(),
            "segments_total": db.count_segments(),
            "videos_by_status": video_counts,
            "jobs": job_counts,
            "clips": clip_counts,
            "latest_jobs": jobs[:5],
            "recent_errors": errors,
            "latest_videos": videos[:10],
        }
    except Exception as exc:
        raise _fail(exc)


@router.get("/jobs")
def jobs(limit: int = 100) -> dict[str, Any]:
    try:
        _init()
        rows = [_job_dict(row) for row in db.list_jobs(limit=limit)]
        return {"jobs": rows, "counts": dict(Counter(row["status"] for row in rows))}
    except Exception as exc:
        raise _fail(exc)


@router.get("/videos")
def videos() -> dict[str, Any]:
    try:
        _init()
        rows = [_video_dict(row) for row in db.list_videos_with_counts()]
        return {"videos": rows, "counts": dict(Counter(row["review_status"] for row in rows))}
    except Exception as exc:
        raise _fail(exc)


@router.get("/clips")
def clips(status: str | None = None, limit: int = 500) -> dict[str, Any]:
    try:
        _init()
        rows = [_clip_dict(row) for row in db.list_clips(status=status if status != "all" else None, limit=limit)]
        all_counts = db.count_clips_by_status()
        return {"clips": rows, "counts": all_counts}
    except Exception as exc:
        raise _fail(exc)


@router.post("/split")
def split(req: SplitRequest) -> dict[str, Any]:
    try:
        _init()
        if req.kind == "folder":
            items = split_video_folder(
                Path(req.path),
                segment_seconds=req.seconds,
                skip_specs=req.skip,
                dry_run=req.dry_run,
                overwrite=req.overwrite,
            )
            files = [_folder_item(item) for item in items]
            return {
                "kind": "folder",
                "status": "preview" if req.dry_run else "done",
                "dry_run": req.dry_run,
                "files": files,
                "files_count": len(files),
                "ok_count": sum(1 for item in files if item["status"] == "ok"),
                "failed_count": sum(1 for item in files if item["status"] == "failed"),
                "segments_count": sum((item.get("result") or {}).get("segments_count", 0) for item in files),
            }
        if req.kind != "file":
            raise ValueError("kind must be 'file' or 'folder'")
        result = split_video_file(
            Path(req.path),
            segment_seconds=req.seconds,
            skip_specs=req.skip,
            dry_run=req.dry_run,
            overwrite=req.overwrite,
        )
        data = _split_result(result)
        data["kind"] = "file"
        return data
    except Exception as exc:
        raise _fail(exc)


# Compatibility endpoints for the earlier UI layer.
@router.post("/split-dry-run")
def split_dry_run(req: SplitRequest) -> dict[str, Any]:
    req.kind = "file"
    req.dry_run = True
    return split(req)


@router.post("/split-jobs")
def split_jobs(req: SplitRequest) -> dict[str, Any]:
    req.kind = "file"
    req.dry_run = False
    return split(req)


@router.post("/split-folder-dry-run")
def split_folder_dry_run(req: SplitRequest) -> dict[str, Any]:
    req.kind = "folder"
    req.dry_run = True
    return split(req)


@router.post("/split-folder-jobs")
def split_folder_jobs(req: SplitRequest) -> dict[str, Any]:
    req.kind = "folder"
    req.dry_run = False
    return split(req)


@router.post("/render")
def render(req: RenderRequest) -> dict[str, Any]:
    try:
        _init()
        results = render_queued(limit=req.limit)
        return {"count": len(results), "rendered": [{"clip_id": cid, "path": str(path)} for cid, path in results]}
    except Exception as exc:
        raise _fail(exc)


@router.post("/retry-failed")
def retry_failed(req: RetryFailedRequest | None = None) -> dict[str, Any]:
    try:
        _init()
        reset_ids, skipped_ids = retry_failed_clips(clip_id=req.clip_id if req else None)
        return {"reset_ids": reset_ids, "skipped_ids": skipped_ids, "reset_count": len(reset_ids), "skipped_count": len(skipped_ids)}
    except Exception as exc:
        raise _fail(exc)


@router.get("/doctor")
def doctor() -> dict[str, Any]:
    _init()
    checks: dict[str, str] = {}
    for binary in ("ffmpeg", "ffprobe"):
        try:
            checks[binary] = f"OK: {require_binary(binary)}"
        except Exception as exc:
            checks[binary] = f"ERROR: {exc}"
    try:
        from ..mpv_session import LUA_SCRIPT, require_mpv
        checks["mpv"] = f"OK: {require_mpv()}"
        checks["lua"] = f"{'OK' if LUA_SCRIPT.exists() else 'MISSING'}: {LUA_SCRIPT}"
    except Exception as exc:
        checks["mpv"] = f"ERROR: {exc}"
    checks["data_dir"] = str(data_dir())
    checks["input_dir"] = str(input_dir())
    checks["output_dir"] = str(output_dir())
    checks["logs_dir"] = str(logs_dir())
    checks["db_path"] = str(db_path())
    return checks
