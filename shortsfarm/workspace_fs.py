"""Managed filesystem workspace with strict root containment."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from . import db
from .services import VIDEO_EXTENSIONS, get_or_add_video, safe_filename


WORKSPACE_ROOT_SETTING = "workspace_root"
SYSTEM_FOLDERS = ("sources", "cuts", "prepared", "edits", "ready", "published")
INTERNAL_FOLDER = ".shortsfarm"
FOLDER_KINDS = {
    "custom", "collection", "project", "source_group", "podcast", "episode",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".ass", ".ssa"}
TEXT_EXTENSIONS = {".txt", ".md", ".json", ".csv", ".tsv"}
_FORBIDDEN_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1F]')


def get_workspace_root() -> Path | None:
    value = db.get_setting(WORKSPACE_ROOT_SETTING)
    if not value:
        return None
    configured = Path(value).expanduser()
    if not configured.is_absolute():
        raise ValueError("Сохранённый workspace_root не является абсолютным путём.")
    if configured.is_symlink():
        raise PermissionError("workspace_root не может быть symlink.")
    return configured.resolve()


def set_workspace_root(path: str | Path) -> Path:
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raise ValueError("workspace_root должен быть абсолютным путём.")
    if raw.is_symlink():
        raise PermissionError("workspace_root не может быть symlink.")
    root = raw.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError("workspace_root должен быть папкой.")
    ensure_workspace_layout(root)
    internal = root / INTERNAL_FOLDER
    try:
        with tempfile.NamedTemporaryFile(
            dir=internal,
            prefix=".write-test-",
            delete=True,
        ):
            pass
    except OSError as exc:
        raise PermissionError(f"Нет доступа на запись в workspace_root: {root}") from exc
    db.set_setting(WORKSPACE_ROOT_SETTING, str(root))
    return root


def ensure_workspace_layout(root: Path | None = None) -> dict[str, Path]:
    resolved = (root or get_workspace_root())
    if resolved is None:
        raise ValueError("workspace_root не настроен.")
    resolved = resolved.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    layout: dict[str, Path] = {}
    for name in SYSTEM_FOLDERS:
        folder = resolved / name
        folder.mkdir(parents=True, exist_ok=True)
        layout[name] = folder
    metadata = resolved / INTERNAL_FOLDER / "metadata"
    metadata.mkdir(parents=True, exist_ok=True)
    layout["metadata"] = metadata
    return layout


def _relative_path(value: str | Path, *, allow_empty: bool = True) -> PurePosixPath:
    text = str(value or "").strip()
    if not text:
        if allow_empty:
            return PurePosixPath()
        raise ValueError("Относительный путь не может быть пустым.")
    if "\\" in text:
        raise ValueError("Используйте '/' в относительных workspace paths.")
    if re.match(r"^[A-Za-z]:/", text):
        raise ValueError("Абсолютные пути в filesystem API запрещены.")
    candidate = PurePosixPath(text)
    if candidate.is_absolute():
        raise ValueError("Абсолютные пути в filesystem API запрещены.")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("Path traversal в workspace запрещён.")
    if candidate.parts and candidate.parts[0] == INTERNAL_FOLDER:
        raise PermissionError("Доступ к .shortsfarm через filesystem API запрещён.")
    return candidate


def _check_no_symlink(root: Path, relative: PurePosixPath) -> None:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PermissionError("Symlinks в managed workspace не поддерживаются.")


def resolve_workspace_path(relative_path: str | Path = "") -> Path:
    root = get_workspace_root()
    if root is None:
        raise ValueError("workspace_root не настроен.")
    relative = _relative_path(relative_path)
    _check_no_symlink(root, relative)
    candidate = (root / Path(*relative.parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PermissionError("Путь находится вне workspace_root.") from exc
    return candidate


def is_inside_workspace(path: Path) -> bool:
    root = get_workspace_root()
    if root is None:
        return False
    try:
        path.expanduser().resolve().relative_to(root)
        return True
    except ValueError:
        return False


def safe_folder_name(value: str) -> str:
    raw = str(value or "").strip()
    if not raw or raw in {".", ".."} or "/" in raw or "\\" in raw:
        raise ValueError("Некорректное имя файла или папки.")
    cleaned = _FORBIDDEN_NAME.sub("_", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    if not cleaned:
        raise ValueError("Имя файла или папки пустое после очистки.")
    return cleaned[:120]


def _relative_string(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    return "" if relative == Path(".") else relative.as_posix()


def _folder_kind(relative_path: str, metadata: Any | None) -> str:
    if "/" not in relative_path and relative_path in SYSTEM_FOLDERS:
        return relative_path
    return str(metadata["kind"]) if metadata is not None else "custom"


def _media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in SUBTITLE_EXTENSIONS:
        return "subtitle"
    if suffix in TEXT_EXTENSIONS:
        return "text"
    return "file"


def _modified_at(path: Path) -> str:
    return datetime.fromtimestamp(
        path.stat().st_mtime,
        tz=timezone.utc,
    ).isoformat()


def _visible_child(path: Path) -> bool:
    return not path.name.startswith(".") and not path.is_symlink()


def list_workspace_dir(relative_path: str = "") -> dict[str, Any]:
    root = get_workspace_root()
    if root is None:
        raise ValueError("workspace_root не настроен.")
    folder = resolve_workspace_path(relative_path)
    if not folder.exists():
        raise FileNotFoundError(f"Workspace folder не найдена: {relative_path}")
    if not folder.is_dir():
        raise ValueError("Запрошенный workspace path не является папкой.")

    normalized_path = _relative_string(folder, root)
    metadata_rows = db.list_workspace_folder_metadata(str(root))
    metadata: dict[str, Any] = {}
    for row in metadata_rows:
        metadata_path = str(row["relative_path"])
        try:
            candidate = resolve_workspace_path(metadata_path)
        except (ValueError, PermissionError):
            continue
        if candidate.is_dir():
            metadata[metadata_path] = row
    items: list[dict[str, Any]] = []
    for child in folder.iterdir():
        if not _visible_child(child):
            continue
        try:
            relative = _relative_string(child, root)
            if child.is_dir():
                children_count = sum(
                    1 for nested in child.iterdir() if _visible_child(nested)
                )
                row = metadata.get(relative)
                items.append({
                    "name": child.name,
                    "display_name": (
                        str(row["display_name"])
                        if row is not None and row["display_name"]
                        else child.name
                    ),
                    "path": relative,
                    "type": "folder",
                    "kind": _folder_kind(relative, row),
                    "size": None,
                    "modified_at": _modified_at(child),
                    "children_count": children_count,
                })
            elif child.is_file():
                items.append({
                    "name": child.name,
                    "path": relative,
                    "type": "file",
                    "media_type": _media_type(child),
                    "size": int(child.stat().st_size),
                    "modified_at": _modified_at(child),
                    "duration_sec": None,
                })
        except (OSError, PermissionError):
            continue
    items.sort(key=lambda item: (
        0 if item["type"] == "folder" else 1,
        str(item["name"]).casefold(),
    ))

    breadcrumbs: list[dict[str, str]] = []
    accumulated: list[str] = []
    for part in PurePosixPath(normalized_path).parts:
        accumulated.append(part)
        breadcrumbs.append({"name": part, "path": "/".join(accumulated)})
    return {
        "path": normalized_path,
        "breadcrumbs": breadcrumbs,
        "items": items,
    }


def create_workspace_folder(
    relative_path: str,
    name: str,
    *,
    kind: str = "custom",
) -> Path:
    normalized_kind = str(kind or "custom").strip().lower()
    if normalized_kind not in FOLDER_KINDS:
        raise ValueError("Некорректный kind workspace folder.")
    parent = resolve_workspace_path(relative_path)
    if not parent.is_dir():
        raise FileNotFoundError("Родительская workspace folder не найдена.")
    target = parent / safe_folder_name(name)
    if target.exists():
        raise FileExistsError(f"Workspace item уже существует: {target.name}")
    target.mkdir()
    root = get_workspace_root()
    assert root is not None
    relative = _relative_string(target, root)
    db.upsert_workspace_folder_metadata(
        str(root),
        relative,
        display_name=target.name,
        kind=normalized_kind,
    )
    return target


def _assert_mutable(relative: PurePosixPath) -> None:
    if not relative.parts:
        raise PermissionError("Нельзя изменить workspace_root.")
    if len(relative.parts) == 1 and relative.parts[0] in SYSTEM_FOLDERS:
        raise PermissionError("Системную workspace folder нельзя изменить.")


def rename_workspace_item(relative_path: str, new_name: str) -> Path:
    relative = _relative_path(relative_path, allow_empty=False)
    _assert_mutable(relative)
    source = resolve_workspace_path(relative_path)
    if not source.exists():
        raise FileNotFoundError("Workspace item не найден.")
    target = source.with_name(safe_folder_name(new_name))
    if target.exists():
        raise FileExistsError(f"Workspace item уже существует: {target.name}")
    source.rename(target)
    root = get_workspace_root()
    assert root is not None
    target_relative = _relative_string(target, root)
    db.delete_workspace_folder_metadata(
        str(root),
        target_relative,
        include_descendants=True,
    )
    db.move_workspace_folder_metadata(
        str(root),
        relative.as_posix(),
        target_relative,
    )
    return target


def move_workspace_item(
    source_relative_path: str,
    target_folder_relative_path: str,
) -> Path:
    source_relative = _relative_path(source_relative_path, allow_empty=False)
    _assert_mutable(source_relative)
    source = resolve_workspace_path(source_relative_path)
    target_folder = resolve_workspace_path(target_folder_relative_path)
    if not source.exists():
        raise FileNotFoundError("Workspace item не найден.")
    if not target_folder.is_dir():
        raise FileNotFoundError("Target workspace folder не найдена.")
    target = target_folder / source.name
    if target.exists():
        raise FileExistsError(f"Workspace item уже существует: {target.name}")
    if source.is_dir():
        try:
            target_folder.relative_to(source)
        except ValueError:
            pass
        else:
            raise ValueError("Нельзя переместить папку внутрь самой себя.")
    shutil.move(str(source), str(target))
    root = get_workspace_root()
    assert root is not None
    target_relative = _relative_string(target, root)
    db.delete_workspace_folder_metadata(
        str(root),
        target_relative,
        include_descendants=True,
    )
    db.move_workspace_folder_metadata(
        str(root),
        source_relative.as_posix(),
        target_relative,
    )
    return target


def delete_workspace_item(
    relative_path: str,
    *,
    recursive: bool = False,
) -> bool:
    relative = _relative_path(relative_path, allow_empty=False)
    _assert_mutable(relative)
    target = resolve_workspace_path(relative_path)
    if not target.exists():
        return False
    if target.is_dir():
        if recursive:
            shutil.rmtree(target)
        else:
            target.rmdir()
    else:
        target.unlink()
    root = get_workspace_root()
    assert root is not None
    db.delete_workspace_folder_metadata(
        str(root),
        relative.as_posix(),
        include_descendants=True,
    )
    return True


def _unique_import_target(folder: Path, name: str) -> Path:
    candidate = folder / name
    if not candidate.exists():
        return candidate
    stem = Path(name).stem
    suffix = Path(name).suffix
    index = 2
    while True:
        candidate = folder / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def import_source_file(
    source_path: str | Path,
    target_folder: str,
    *,
    mode: str = "copy",
) -> tuple[Path, int]:
    if mode != "copy":
        raise ValueError("Этап 1 поддерживает только import mode=copy.")
    unresolved_source = Path(source_path).expanduser()
    if unresolved_source.is_symlink():
        raise PermissionError("Импорт symlink запрещён.")
    source = unresolved_source.resolve()
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Source video не найден: {source}")
    if source.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("Можно импортировать только поддерживаемые video files.")
    target_dir = resolve_workspace_path(target_folder)
    root = get_workspace_root()
    assert root is not None
    sources_root = root / "sources"
    try:
        target_dir.relative_to(sources_root)
    except ValueError as exc:
        raise PermissionError("Source files можно импортировать только в sources/.") from exc
    if not target_dir.is_dir():
        raise FileNotFoundError("Target source folder не найдена.")

    target_name = f"{safe_filename(source.stem)}{source.suffix.lower()}"
    target = _unique_import_target(target_dir, target_name)
    temp = target.with_name(f".{target.name}.importing")
    try:
        shutil.copy2(source, temp)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    video_id = get_or_add_video(target)
    return target, video_id


def register_workspace_source(relative_path: str) -> tuple[Path, int]:
    path = resolve_workspace_path(relative_path)
    if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("Workspace item не является поддерживаемым video file.")
    return path, get_or_add_video(path)


def workspace_source_relative_path(source_path: str | Path) -> str | None:
    root = get_workspace_root()
    if root is None:
        return None
    unresolved = Path(source_path).expanduser()
    if unresolved.is_symlink():
        raise PermissionError("Managed source не может быть symlink.")
    source = unresolved.resolve()
    try:
        relative = source.relative_to(root)
    except ValueError:
        return None
    if not relative.parts or relative.parts[0] != "sources":
        return None
    _check_no_symlink(root, PurePosixPath(relative.as_posix()))
    return relative.as_posix()


def build_cut_output_dir(
    source_relative_path: str,
    aspect: str,
    *,
    create: bool = True,
) -> Path:
    normalized_aspect = str(aspect or "").strip().lower()
    if normalized_aspect not in {"original", "16x9", "9x16"}:
        raise ValueError("aspect должен быть original, 16x9 или 9x16.")
    relative = _relative_path(source_relative_path, allow_empty=False)
    if not relative.parts or relative.parts[0] != "sources":
        raise ValueError("Source path должен находиться внутри sources/.")
    if len(relative.parts) < 2:
        raise ValueError("Source path должен указывать на файл внутри sources/.")
    root = get_workspace_root()
    if root is None:
        raise ValueError("workspace_root не настроен.")
    source_subpath = Path(*relative.parts[1:])
    output = (
        root
        / "cuts"
        / source_subpath.parent
        / safe_filename(source_subpath.stem)
        / normalized_aspect
    )
    if create:
        output.mkdir(parents=True, exist_ok=True)
    return output


def build_prepared_output_dir(
    source_relative_path: str,
    aspect: str,
    *,
    create: bool = True,
) -> Path:
    normalized_aspect = str(aspect or "").strip().lower()
    if normalized_aspect not in {"16x9", "9x16"}:
        raise ValueError("aspect должен быть 16x9 или 9x16.")
    relative = _relative_path(source_relative_path, allow_empty=False)
    if not relative.parts or relative.parts[0] != "sources":
        raise ValueError("Source path должен находиться внутри sources/.")
    if len(relative.parts) < 2:
        raise ValueError("Source path должен указывать на файл внутри sources/.")
    root = get_workspace_root()
    if root is None:
        raise ValueError("workspace_root не настроен.")
    source_subpath = Path(*relative.parts[1:])
    output = (
        root
        / "prepared"
        / source_subpath.parent
        / safe_filename(source_subpath.stem)
        / normalized_aspect
    )
    if create:
        output.mkdir(parents=True, exist_ok=True)
    return output


def build_edit_output_path(
    source_relative_path: str,
    item_type: str,
    item_id: int,
    job_id: int,
    *,
    create: bool = True,
) -> Path:
    normalized_type = str(item_type or "").strip().lower()
    if normalized_type not in {"segment", "clip"}:
        raise ValueError("item_type должен быть segment или clip.")
    if int(item_id) <= 0 or int(job_id) <= 0:
        raise ValueError("item_id и job_id должны быть положительными integer.")
    relative = _relative_path(source_relative_path, allow_empty=False)
    if not relative.parts or relative.parts[0] != "sources":
        raise ValueError("Source path должен находиться внутри sources/.")
    if len(relative.parts) < 2:
        raise ValueError("Source path должен указывать на файл внутри sources/.")
    root = get_workspace_root()
    if root is None:
        raise ValueError("workspace_root не настроен.")

    source_subpath = Path(*relative.parts[1:])
    output = (
        root
        / "edits"
        / source_subpath.parent
        / safe_filename(source_subpath.stem)
        / f"{normalized_type}_{int(item_id):03d}"
        / f"edit_job_{int(job_id)}.mp4"
    )
    if create:
        output.parent.mkdir(parents=True, exist_ok=True)
    return output
