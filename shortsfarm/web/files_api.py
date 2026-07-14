from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from ..config import data_dir, input_dir, output_dir
from ..ffmpeg_tools import probe_duration, require_binary
from ..local_dialogs import LocalDialogUnavailable, pick_directory_dialog
from ..mpv_session import require_mpv
from ..services import VIDEO_EXTENSIONS
from ..workspace_fs import (
    SYSTEM_FOLDERS,
    create_workspace_folder,
    delete_workspace_item as delete_managed_workspace_item,
    get_workspace_root,
    import_source_file,
    list_workspace_dir,
    move_workspace_item as move_managed_workspace_item,
    register_workspace_source,
    rename_workspace_item as rename_managed_workspace_item,
    set_workspace_root,
)
from .api_common import fail, init_api
from .schemas import (
    FileFolderCreateRequest,
    FileImportSourceRequest,
    FileMoveRequest,
    FileRegisterSourceRequest,
    FileRenameRequest,
    OpenMpvRequest,
    WorkspaceRootRequest,
)

router = APIRouter()


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    sec = total % 60
    return f"{hours}:{minutes:02d}:{sec:02d}" if hours else f"{minutes}:{sec:02d}"


def _workspace_settings_payload() -> dict[str, Any]:
    root = get_workspace_root()
    if root is None:
        return {
            "workspace_root": None,
            "exists": False,
            "layout": {},
        }
    exists = root.exists() and root.is_dir()
    layout = {
        name: str(root / name)
        for name in SYSTEM_FOLDERS
        if exists and (root / name).is_dir()
    }
    return {
        "workspace_root": str(root),
        "exists": exists,
        "layout": layout,
    }


def _resolve_fs_path(value: str | None = None) -> Path:
    if not value:
        return Path.home().resolve()
    return Path(value).expanduser().resolve()


def _resolve_video_path(value: str) -> Path:
    video = _resolve_fs_path(value)
    if not video.exists():
        raise FileNotFoundError(f"Видео не найдено: {video}")
    if not video.is_file():
        raise ValueError(f"Это не файл: {video}")
    if video.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError(f"Это не поддерживаемый видеофайл: {video.name}")
    return video


def _mtime_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _fs_root(label: str, path: Path) -> dict[str, str] | None:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return None
    if not resolved.exists() or not resolved.is_dir():
        return None
    return {"label": label, "path": str(resolved)}


def _fs_item(path: Path) -> dict[str, Any] | None:
    try:
        if path.name.startswith("."):
            return None
        stat = path.stat()
        if path.is_dir():
            return {
                "type": "dir",
                "name": path.name,
                "path": str(path),
                "size": None,
                "mtime": _mtime_iso(stat.st_mtime),
            }
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            return {
                "type": "video",
                "name": path.name,
                "path": str(path),
                "size": int(stat.st_size),
                "mtime": _mtime_iso(stat.st_mtime),
                "ext": path.suffix.lower(),
            }
    except (OSError, PermissionError):
        return None
    return None


def _thumbnail_cache_path(video: Path) -> Path:
    stat = video.stat()
    key = f"{video}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8", errors="ignore")
    name = hashlib.sha256(key).hexdigest()[:32] + ".jpg"
    cache_dir = data_dir() / "cache" / "thumbnails"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / name


def _thumbnail_placeholder(message: str = "Нет миниатюры") -> Response:
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="96" height="54" viewBox="0 0 96 54">
  <rect width="96" height="54" rx="8" fill="#273244"/>
  <path d="M40 18v18l17-9z" fill="#94a3b8"/>
  <text x="48" y="47" text-anchor="middle" font-family="Arial,sans-serif" font-size="7" fill="#cbd5e1">{message}</text>
</svg>"""
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/settings/workspace")
def workspace_settings_get() -> dict[str, Any]:
    try:
        init_api()
        return _workspace_settings_payload()
    except Exception as exc:
        raise fail(exc)


@router.post("/settings/workspace")
def workspace_settings_save(req: WorkspaceRootRequest) -> dict[str, Any]:
    try:
        init_api()
        set_workspace_root(req.workspace_root)
        return _workspace_settings_payload()
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except Exception as exc:
        raise fail(exc)


@router.post("/settings/workspace/pick-directory")
def workspace_settings_pick_directory() -> dict[str, Any]:
    try:
        init_api()
        selected_path = pick_directory_dialog()
        if selected_path is None:
            return {
                "selected": False,
                "workspace_root": _workspace_settings_payload()["workspace_root"],
            }
        set_workspace_root(selected_path)
        return {
            "selected": True,
            **_workspace_settings_payload(),
        }
    except LocalDialogUnavailable as exc:
        raise fail(exc, status_code=409)
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except Exception as exc:
        raise fail(exc)


@router.get("/files")
def files_list(path: str = "") -> dict[str, Any]:
    try:
        init_api()
        return list_workspace_dir(path)
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.post("/files/folder")
def files_folder_create(req: FileFolderCreateRequest) -> dict[str, Any]:
    try:
        init_api()
        created = create_workspace_folder(
            req.parent_path,
            req.name,
            kind=req.kind,
        )
        root = get_workspace_root()
        if root is None:
            raise ValueError("workspace_root не настроен.")
        return {
            "status": "ok",
            "path": created.relative_to(root).as_posix(),
            "name": created.name,
            "kind": req.kind,
        }
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except FileExistsError as exc:
        raise fail(exc, status_code=409)
    except Exception as exc:
        raise fail(exc)


@router.patch("/files/rename")
def files_rename(req: FileRenameRequest) -> dict[str, Any]:
    try:
        init_api()
        renamed = rename_managed_workspace_item(req.path, req.new_name)
        root = get_workspace_root()
        if root is None:
            raise ValueError("workspace_root не настроен.")
        return {
            "status": "ok",
            "path": renamed.relative_to(root).as_posix(),
            "name": renamed.name,
        }
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except FileExistsError as exc:
        raise fail(exc, status_code=409)
    except Exception as exc:
        raise fail(exc)


@router.post("/files/move")
def files_move(req: FileMoveRequest) -> dict[str, Any]:
    try:
        init_api()
        moved = move_managed_workspace_item(
            req.source_path,
            req.target_folder,
        )
        root = get_workspace_root()
        if root is None:
            raise ValueError("workspace_root не настроен.")
        return {
            "status": "ok",
            "path": moved.relative_to(root).as_posix(),
        }
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except FileExistsError as exc:
        raise fail(exc, status_code=409)
    except Exception as exc:
        raise fail(exc)


@router.delete("/files")
def files_delete(path: str, recursive: bool = False) -> dict[str, Any]:
    try:
        init_api()
        deleted = delete_managed_workspace_item(path, recursive=recursive)
        return {"status": "ok", "path": path, "deleted": deleted}
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except OSError as exc:
        raise fail(
            ValueError(
                "Папка не пуста. Подтвердите recursive delete."
                if not recursive
                else str(exc)
            )
        )
    except Exception as exc:
        raise fail(exc)


@router.post("/files/import-source")
def files_import_source(req: FileImportSourceRequest) -> dict[str, Any]:
    try:
        init_api()
        imported, video_id = import_source_file(
            req.source_path,
            req.target_folder,
            mode=req.mode,
        )
        root = get_workspace_root()
        if root is None:
            raise ValueError("workspace_root не настроен.")
        return {
            "status": "ok",
            "path": imported.relative_to(root).as_posix(),
            "name": imported.name,
            "video_id": video_id,
        }
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.post("/files/register-source")
def files_register_source(req: FileRegisterSourceRequest) -> dict[str, Any]:
    try:
        init_api()
        path, video_id = register_workspace_source(req.path)
        root = get_workspace_root()
        if root is None:
            raise ValueError("workspace_root не настроен.")
        return {
            "status": "ok",
            "path": path.relative_to(root).as_posix(),
            "video_id": video_id,
        }
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.get("/fs/roots")
def fs_roots() -> dict[str, Any]:
    init_api()
    candidates = [
        _fs_root("Дом", Path.home()),
        _fs_root("Видео", Path.home() / "Videos"),
        _fs_root("Проект", Path.cwd()),
        _fs_root("Input", input_dir()),
        _fs_root("Output", output_dir()),
    ]

    roots: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in candidates:
        if item is None or item["path"] in seen:
            continue
        roots.append(item)
        seen.add(item["path"])
    return {"roots": roots}


@router.get("/fs/list")
def fs_list(path: str | None = None) -> dict[str, Any]:
    try:
        init_api()
        folder = _resolve_fs_path(path)
        if not folder.exists():
            raise FileNotFoundError(f"Папка не найдена: {folder}")
        if not folder.is_dir():
            raise ValueError(f"Это не папка: {folder}")

        items: list[dict[str, Any]] = []
        try:
            children = list(folder.iterdir())
        except PermissionError as exc:
            raise PermissionError(f"Нет доступа к папке: {folder}") from exc

        for child in children:
            item = _fs_item(child)
            if item is not None:
                items.append(item)

        items.sort(key=lambda item: (0 if item["type"] == "dir" else 1, item["name"].casefold()))
        parent = str(folder.parent) if folder.parent != folder else None
        return {"path": str(folder), "parent": parent, "items": items}
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.get("/fs/video-info")
def fs_video_info(path: str) -> dict[str, Any]:
    try:
        init_api()
        video = _resolve_video_path(path)

        stat = video.stat()
        duration = probe_duration(video)
        return {
            "path": str(video),
            "name": video.name,
            "duration_sec": duration,
            "duration_text": _format_duration(duration),
            "size": int(stat.st_size),
            "mtime": _mtime_iso(stat.st_mtime),
            "ext": video.suffix.lower(),
        }
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except Exception as exc:
        raise fail(exc)


@router.get("/fs/thumbnail")
def fs_thumbnail(path: str) -> Response:
    try:
        init_api()
        video = _resolve_video_path(path)
        thumb = _thumbnail_cache_path(video)

        if not thumb.exists():
            ffmpeg = require_binary("ffmpeg")
            commands = [
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    "1",
                    "-i",
                    str(video),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=160:90:force_original_aspect_ratio=increase,crop=160:90",
                    "-q:v",
                    "4",
                    str(thumb),
                ],
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(video),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=160:90:force_original_aspect_ratio=increase,crop=160:90",
                    "-q:v",
                    "4",
                    str(thumb),
                ],
            ]

            ok = False
            for cmd in commands:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=20,
                    check=False,
                )
                if result.returncode == 0 and thumb.exists() and thumb.stat().st_size > 0:
                    ok = True
                    break
            if not ok:
                try:
                    thumb.unlink(missing_ok=True)
                except OSError:
                    pass
                return _thumbnail_placeholder("Нет кадра")

        return FileResponse(
            thumb,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception:
        return _thumbnail_placeholder("Нет кадра")


@router.post("/fs/open-mpv")
def fs_open_mpv(req: OpenMpvRequest) -> dict[str, Any]:
    try:
        init_api()
        video = _resolve_video_path(req.path)
        mpv = require_mpv()
        subprocess.Popen(
            [mpv, str(video)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"status": "opened", "path": str(video), "player": "mpv"}
    except FileNotFoundError as exc:
        raise fail(exc, status_code=404)
    except PermissionError as exc:
        raise fail(exc, status_code=403)
    except Exception as exc:
        raise fail(exc)
