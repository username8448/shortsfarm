"""Tests for the managed workspace filesystem and its API."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException


def _set_root(tmp_path: Path, name: str = "workspace") -> Path:
    from shortsfarm.workspace_fs import set_workspace_root

    return set_workspace_root(tmp_path / name)


def test_workspace_root_setting_and_base_layout(tmp_path):
    from shortsfarm import db
    from shortsfarm.workspace_fs import (
        SYSTEM_FOLDERS,
        ensure_workspace_layout,
        get_workspace_root,
        list_workspace_dir,
    )

    root = _set_root(tmp_path)
    layout = ensure_workspace_layout()

    assert get_workspace_root() == root.resolve()
    assert db.get_setting("workspace_root") == str(root.resolve())
    assert all((root / name).is_dir() for name in SYSTEM_FOLDERS)
    assert layout["metadata"] == root / ".shortsfarm" / "metadata"
    assert layout["metadata"].is_dir()
    root_list = list_workspace_dir()
    assert root_list["path"] == ""
    assert {item["name"] for item in root_list["items"]} == set(SYSTEM_FOLDERS)


def test_workspace_pick_directory_saves_selected_root(tmp_path, monkeypatch):
    from shortsfarm import db
    from shortsfarm.web import api

    selected = tmp_path / "picked-workspace"
    saved_paths: list[str] = []
    real_set_workspace_root = api.set_workspace_root

    def tracked_set_workspace_root(path):
        saved_paths.append(path)
        return real_set_workspace_root(path)

    monkeypatch.setattr(
        api,
        "pick_directory_dialog",
        lambda: str(selected),
    )
    monkeypatch.setattr(api, "set_workspace_root", tracked_set_workspace_root)

    data = api.workspace_settings_pick_directory()

    assert data["selected"] is True
    assert saved_paths == [str(selected)]
    assert data["workspace_root"] == str(selected.resolve())
    assert db.get_setting("workspace_root") == str(selected.resolve())


def test_workspace_pick_directory_cancel_keeps_current_root(tmp_path, monkeypatch):
    from shortsfarm.web import api

    current = _set_root(tmp_path, "current-workspace")
    monkeypatch.setattr(api, "pick_directory_dialog", lambda: None)
    monkeypatch.setattr(
        api,
        "set_workspace_root",
        lambda path: pytest.fail("set_workspace_root must not run after cancel"),
    )

    data = api.workspace_settings_pick_directory()

    assert data == {
        "selected": False,
        "workspace_root": str(current.resolve()),
    }
    assert api.workspace_settings_get()["workspace_root"] == str(current.resolve())


def test_workspace_pick_directory_unavailable_returns_http_409(monkeypatch):
    from shortsfarm.local_dialogs import (
        UNAVAILABLE_MESSAGE,
        LocalDialogUnavailable,
    )
    from shortsfarm.web import api

    def unavailable():
        raise LocalDialogUnavailable(UNAVAILABLE_MESSAGE)

    monkeypatch.setattr(api, "pick_directory_dialog", unavailable)

    with pytest.raises(HTTPException) as exc:
        api.workspace_settings_pick_directory()

    assert exc.value.status_code == 409
    assert exc.value.detail["message"] == UNAVAILABLE_MESSAGE


def test_workspace_pick_directory_creates_layout(tmp_path, monkeypatch):
    from shortsfarm.web import api
    from shortsfarm.workspace_fs import SYSTEM_FOLDERS

    selected = tmp_path / "picked-layout"
    monkeypatch.setattr(
        api,
        "pick_directory_dialog",
        lambda: str(selected),
    )

    data = api.workspace_settings_pick_directory()

    assert data["exists"] is True
    assert set(data["layout"]) == set(SYSTEM_FOLDERS)
    assert all((selected / name).is_dir() for name in SYSTEM_FOLDERS)


def test_local_dialog_pick_file_returns_selected_path(tmp_path, monkeypatch):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalDialogPickRequest

    selected = tmp_path / "reaction.mp4"
    monkeypatch.setattr(api, "pick_file_dialog", lambda title: str(selected))

    data = api.local_dialog_pick(
        LocalDialogPickRequest(kind="file", title="Выберите reaction")
    )

    assert data == {"selected": True, "path": str(selected)}


def test_local_dialog_pick_directory_cancel(monkeypatch):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalDialogPickRequest

    monkeypatch.setattr(api, "pick_directory_dialog", lambda title: None)

    data = api.local_dialog_pick(LocalDialogPickRequest(kind="directory"))

    assert data == {"selected": False, "path": None}


def test_local_dialog_pick_unavailable_returns_http_409(monkeypatch):
    from shortsfarm.local_dialogs import (
        UNAVAILABLE_FILE_MESSAGE,
        LocalDialogUnavailable,
    )
    from shortsfarm.web import api
    from shortsfarm.web.schemas import LocalDialogPickRequest

    def unavailable(title):
        raise LocalDialogUnavailable(UNAVAILABLE_FILE_MESSAGE)

    monkeypatch.setattr(api, "pick_file_dialog", unavailable)

    with pytest.raises(HTTPException) as exc:
        api.local_dialog_pick(LocalDialogPickRequest(kind="file"))

    assert exc.value.status_code == 409
    assert exc.value.detail["message"] == UNAVAILABLE_FILE_MESSAGE


def test_manual_workspace_settings_still_work(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import WorkspaceRootRequest

    manual = tmp_path / "manual-workspace"

    data = api.workspace_settings_save(
        WorkspaceRootRequest(workspace_root=str(manual))
    )

    assert data["workspace_root"] == str(manual.resolve())
    assert data["exists"] is True


def test_workspace_path_rejects_traversal_absolute_internal_and_symlink(tmp_path):
    from shortsfarm.workspace_fs import resolve_workspace_path

    root = _set_root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(ValueError, match="traversal|Traversal"):
        resolve_workspace_path("../outside")
    with pytest.raises(ValueError, match="Абсолютные"):
        resolve_workspace_path(str(outside))
    with pytest.raises(PermissionError, match="shortsfarm"):
        resolve_workspace_path(".shortsfarm/metadata")

    link = root / "sources" / "outside-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are not available in this environment.")
    with pytest.raises(PermissionError, match="Symlinks"):
        resolve_workspace_path("sources/outside-link")


def test_system_folders_cannot_be_renamed_moved_or_deleted(tmp_path):
    from shortsfarm.workspace_fs import (
        delete_workspace_item,
        move_workspace_item,
        rename_workspace_item,
    )

    _set_root(tmp_path)
    with pytest.raises(PermissionError, match="Системную"):
        delete_workspace_item("sources", recursive=True)
    with pytest.raises(PermissionError, match="Системную"):
        rename_workspace_item("cuts", "old-cuts")
    with pytest.raises(PermissionError, match="Системную"):
        move_workspace_item("ready", "sources")


def test_create_nested_structure_and_metadata_is_scoped_by_root(tmp_path):
    from shortsfarm import db
    from shortsfarm.workspace_fs import create_workspace_folder, set_workspace_root

    first_root = _set_root(tmp_path, "one")
    create_workspace_folder("sources", "Автор", kind="collection")
    create_workspace_folder("sources/Автор", "Подкаст", kind="podcast")
    episode = create_workspace_folder(
        "sources/Автор/Подкаст",
        "Выпуск 001",
        kind="episode",
    )
    assert episode.is_dir()
    assert db.get_workspace_folder_metadata(
        str(first_root),
        "sources/Автор/Подкаст/Выпуск 001",
    )["kind"] == "episode"

    second_root = set_workspace_root(tmp_path / "two")
    create_workspace_folder("sources", "Автор", kind="project")

    assert db.get_workspace_folder_metadata(
        str(first_root),
        "sources/Автор",
    )["kind"] == "collection"
    assert db.get_workspace_folder_metadata(
        str(second_root),
        "sources/Автор",
    )["kind"] == "project"


def test_rename_folder_moves_descendant_metadata(tmp_path):
    from shortsfarm import db
    from shortsfarm.workspace_fs import (
        create_workspace_folder,
        rename_workspace_item,
    )

    root = _set_root(tmp_path)
    create_workspace_folder("sources", "Автор", kind="collection")
    create_workspace_folder("sources/Автор", "Подкаст", kind="podcast")

    renamed = rename_workspace_item("sources/Автор", "Новый Автор")

    assert renamed == root / "sources" / "Новый Автор"
    assert db.get_workspace_folder_metadata(
        str(root),
        "sources/Новый Автор",
    )["kind"] == "collection"
    assert db.get_workspace_folder_metadata(
        str(root),
        "sources/Новый Автор/Подкаст",
    )["kind"] == "podcast"
    assert db.get_workspace_folder_metadata(str(root), "sources/Автор") is None


def test_move_file_and_list_workspace_types(tmp_path):
    from shortsfarm.workspace_fs import (
        create_workspace_folder,
        list_workspace_dir,
        move_workspace_item,
    )

    root = _set_root(tmp_path)
    create_workspace_folder("sources", "Автор")
    create_workspace_folder("sources/Автор", "Выпуск")
    video = root / "sources" / "Автор" / "video.mp4"
    video.write_bytes(b"video")
    (root / "sources" / "Автор" / "cover.jpg").write_bytes(b"image")
    (root / "sources" / "Автор" / "notes.txt").write_text("notes", encoding="utf-8")

    moved = move_workspace_item(
        "sources/Автор/video.mp4",
        "sources/Автор/Выпуск",
    )
    data = list_workspace_dir("sources/Автор")

    assert moved == root / "sources" / "Автор" / "Выпуск" / "video.mp4"
    by_name = {item["name"]: item for item in data["items"]}
    assert by_name["Выпуск"]["type"] == "folder"
    assert by_name["cover.jpg"]["media_type"] == "image"
    assert by_name["notes.txt"]["media_type"] == "text"
    assert data["breadcrumbs"] == [
        {"name": "sources", "path": "sources"},
        {"name": "Автор", "path": "sources/Автор"},
    ]


def test_delete_nonempty_folder_requires_recursive(tmp_path):
    from shortsfarm.workspace_fs import (
        create_workspace_folder,
        delete_workspace_item,
    )

    root = _set_root(tmp_path)
    create_workspace_folder("sources", "Temporary")
    (root / "sources" / "Temporary" / "file.txt").write_text("x", encoding="utf-8")

    with pytest.raises(OSError):
        delete_workspace_item("sources/Temporary")
    assert delete_workspace_item("sources/Temporary", recursive=True)
    assert not (root / "sources" / "Temporary").exists()


def test_import_source_copies_registers_and_adds_suffix(tmp_path, monkeypatch):
    from shortsfarm import db
    from shortsfarm.workspace_fs import import_source_file

    root = _set_root(tmp_path)
    external = tmp_path / "My Video.mp4"
    external.write_bytes(b"external")
    monkeypatch.setattr("shortsfarm.services.probe_duration", lambda path: 42.0)

    first, first_video_id = import_source_file(external, "sources")
    second, second_video_id = import_source_file(external, "sources")

    assert external.exists()
    assert first == root / "sources" / "My_Video.mp4"
    assert second == root / "sources" / "My_Video_2.mp4"
    assert first.read_bytes() == b"external"
    assert db.get_video(first_video_id)["source_path"] == str(first)
    assert db.get_video(second_video_id)["source_path"] == str(second)
    with pytest.raises(ValueError, match="mode=copy"):
        import_source_file(external, "sources", mode="move")
    with pytest.raises(PermissionError, match="sources"):
        import_source_file(external, "ready")


def test_register_source_only_adds_video_record(tmp_path, monkeypatch):
    from shortsfarm import db
    from shortsfarm.workspace_fs import register_workspace_source

    root = _set_root(tmp_path)
    source = root / "sources" / "manual.mp4"
    source.write_bytes(b"manual")
    monkeypatch.setattr("shortsfarm.services.probe_duration", lambda path: 10.0)

    path, video_id = register_workspace_source("sources/manual.mp4")

    assert path == source
    assert db.get_video(video_id)["source_path"] == str(source)
    assert list((root / "cuts").iterdir()) == []


@pytest.mark.parametrize("folder", ["cuts", "prepared", "edits"])
def test_register_source_rejects_video_outside_sources(tmp_path, folder):
    from shortsfarm.workspace_fs import register_workspace_source

    root = _set_root(tmp_path)
    video = root / folder / "result.mp4"
    video.write_bytes(b"result")

    with pytest.raises(
        PermissionError,
        match="только файл из sources/",
    ):
        register_workspace_source(f"{folder}/result.mp4")


def test_register_source_rejects_non_video_inside_sources(tmp_path):
    from shortsfarm.workspace_fs import register_workspace_source

    root = _set_root(tmp_path)
    notes = root / "sources" / "notes.txt"
    notes.write_text("notes", encoding="utf-8")

    with pytest.raises(ValueError, match="video file"):
        register_workspace_source("sources/notes.txt")


def test_register_source_rejects_symlink_inside_sources(tmp_path):
    from shortsfarm.workspace_fs import register_workspace_source

    root = _set_root(tmp_path)
    target = root / "sources" / "target.mp4"
    target.write_bytes(b"target")
    link = root / "sources" / "link.mp4"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlinks are not available in this environment.")

    with pytest.raises(PermissionError, match="Symlinks"):
        register_workspace_source("sources/link.mp4")


def test_register_source_api_rejects_video_from_cuts(tmp_path):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import FileRegisterSourceRequest

    root = _set_root(tmp_path)
    (root / "cuts" / "result.mp4").write_bytes(b"result")

    with pytest.raises(HTTPException) as exc:
        api.files_register_source(
            FileRegisterSourceRequest(path="cuts/result.mp4")
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["message"] == (
        "Как исходник можно зарегистрировать только файл из sources/."
    )


def test_build_cut_output_dir_preserves_sources_hierarchy(tmp_path):
    from shortsfarm.workspace_fs import build_cut_output_dir

    root = _set_root(tmp_path)
    output = build_cut_output_dir(
        "sources/Автор/Подкаст/Выпуск 001/original.mp4",
        "9x16",
    )

    assert output == (
        root / "cuts" / "Автор" / "Подкаст" / "Выпуск 001"
        / "original" / "9x16"
    )
    assert output.is_dir()


def test_workspace_source_relative_path_only_accepts_sources(tmp_path):
    from shortsfarm.workspace_fs import workspace_source_relative_path

    root = _set_root(tmp_path)
    source = root / "sources" / "show" / "episode.mp4"
    source.parent.mkdir()
    source.write_bytes(b"source")
    ready = root / "ready" / "episode.mp4"
    ready.write_bytes(b"ready")
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")

    assert workspace_source_relative_path(source) == "sources/show/episode.mp4"
    assert workspace_source_relative_path(ready) is None
    assert workspace_source_relative_path(outside) is None


def test_build_prepared_output_dir_preserves_sources_hierarchy(tmp_path):
    from shortsfarm.workspace_fs import build_prepared_output_dir

    root = _set_root(tmp_path)
    output = build_prepared_output_dir(
        "sources/Автор/Подкаст/Выпуск 001/original.mp4",
        "16x9",
    )

    assert output == (
        root / "prepared" / "Автор" / "Подкаст" / "Выпуск 001"
        / "original" / "16x9"
    )
    assert output.is_dir()


def test_build_edit_output_path_preserves_sources_hierarchy(tmp_path):
    from shortsfarm.workspace_fs import build_edit_output_path

    root = _set_root(tmp_path)
    output = build_edit_output_path(
        "sources/Автор/Подкаст/Выпуск 001/original.mp4",
        "segment",
        7,
        42,
    )

    assert output == (
        root / "edits" / "Автор" / "Подкаст" / "Выпуск 001"
        / "original" / "segment_007" / "edit_job_42.mp4"
    )
    assert output.parent.is_dir()


def test_files_api_settings_crud_import_and_register(tmp_path, monkeypatch):
    from shortsfarm.web import api
    from shortsfarm.web.schemas import (
        FileFolderCreateRequest,
        FileImportSourceRequest,
        FileMoveRequest,
        FileRegisterSourceRequest,
        FileRenameRequest,
        WorkspaceRootRequest,
    )

    root = tmp_path / "api-workspace"
    settings = api.workspace_settings_save(
        WorkspaceRootRequest(workspace_root=str(root))
    )
    assert settings["workspace_root"] == str(root.resolve())
    assert settings["exists"] is True

    created = api.files_folder_create(
        FileFolderCreateRequest(
            parent_path="sources",
            name="Автор",
            kind="collection",
        )
    )
    assert created["path"] == "sources/Автор"
    listed = api.files_list("sources")
    assert listed["items"][0]["kind"] == "collection"

    renamed = api.files_rename(
        FileRenameRequest(path="sources/Автор", new_name="Автор 2")
    )
    assert renamed["path"] == "sources/Автор 2"
    api.files_folder_create(
        FileFolderCreateRequest(
            parent_path="sources",
            name="Архив",
            kind="custom",
        )
    )

    external = tmp_path / "external.mp4"
    external.write_bytes(b"external")
    monkeypatch.setattr("shortsfarm.services.probe_duration", lambda path: 5.0)
    imported = api.files_import_source(
        FileImportSourceRequest(
            source_path=str(external),
            target_folder="sources/Автор 2",
            mode="copy",
        )
    )
    assert imported["path"] == "sources/Автор 2/external.mp4"

    registered = api.files_register_source(
        FileRegisterSourceRequest(path=imported["path"])
    )
    assert registered["video_id"] == imported["video_id"]

    moved = api.files_move(
        FileMoveRequest(
            source_path=imported["path"],
            target_folder="sources/Архив",
        )
    )
    assert moved["path"] == "sources/Архив/external.mp4"

    deleted = api.files_delete(moved["path"])
    assert deleted["deleted"] is True
    assert api.files_delete("sources/Архив", recursive=True)["deleted"] is True
    with pytest.raises(HTTPException) as exc:
        api.files_delete("sources", recursive=True)
    assert exc.value.status_code == 403
