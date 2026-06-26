"""Tests for local desktop dialog backend selection."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def test_pick_directory_dialog_falls_back_to_yad(tmp_path, monkeypatch):
    import shortsfarm.local_dialogs as dialogs

    selected = tmp_path / "workspace"
    commands: list[list[str]] = []

    monkeypatch.setattr(dialogs, "_has_gui_session", lambda: True)
    monkeypatch.setattr(
        dialogs,
        "_pick_with_tkinter",
        lambda title: (_ for _ in ()).throw(dialogs._DialogBackendUnavailable()),
    )
    monkeypatch.setattr(
        dialogs.shutil,
        "which",
        lambda name: "/usr/bin/yad" if name == "yad" else None,
    )

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=f"{selected}\n",
            stderr="",
        )

    monkeypatch.setattr(dialogs.subprocess, "run", fake_run)

    result = dialogs.pick_directory_dialog()

    assert result == str(selected.resolve())
    assert commands[0][:3] == ["/usr/bin/yad", "--file", "--directory"]


def test_yad_window_close_is_treated_as_cancel(monkeypatch):
    import shortsfarm.local_dialogs as dialogs

    monkeypatch.setattr(
        dialogs.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=252,
            stdout="",
            stderr="",
        ),
    )

    result = dialogs._pick_with_command(
        ["/usr/bin/yad", "--file", "--directory"],
        cancel_codes=(1, 252),
    )

    assert result is None


def test_pick_file_dialog_falls_back_to_zenity(tmp_path, monkeypatch):
    import shortsfarm.local_dialogs as dialogs

    selected = tmp_path / "reaction.mp4"
    commands: list[list[str]] = []

    monkeypatch.setattr(dialogs, "_has_gui_session", lambda: True)
    monkeypatch.setattr(
        dialogs,
        "_pick_file_with_tkinter",
        lambda title: (_ for _ in ()).throw(dialogs._DialogBackendUnavailable()),
    )
    monkeypatch.setattr(
        dialogs.shutil,
        "which",
        lambda name: "/usr/bin/zenity" if name == "zenity" else None,
    )

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=f"{selected}\n",
            stderr="",
        )

    monkeypatch.setattr(dialogs.subprocess, "run", fake_run)

    result = dialogs.pick_file_dialog("Выберите reaction")

    assert result == str(selected.resolve())
    assert commands[0][:2] == ["/usr/bin/zenity", "--file-selection"]
