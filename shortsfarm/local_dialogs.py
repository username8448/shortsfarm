"""Local desktop dialogs used by the ShortsFarm web UI."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


UNAVAILABLE_MESSAGE = (
    "Локальный выбор папки недоступен. Укажите путь вручную."
)


class LocalDialogUnavailable(RuntimeError):
    """Raised when the backend process cannot open a local GUI dialog."""


class _DialogBackendUnavailable(RuntimeError):
    pass


def _normalize_selected_path(value: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise LocalDialogUnavailable(UNAVAILABLE_MESSAGE)
    return str(path.resolve())


def _has_gui_session() -> bool:
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


def _pick_with_tkinter(title: str) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except (ImportError, ModuleNotFoundError) as exc:
        raise _DialogBackendUnavailable from exc

    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        selected = filedialog.askdirectory(
            parent=root,
            title=title,
            mustexist=True,
        )
    except (OSError, RuntimeError, tk.TclError) as exc:
        raise _DialogBackendUnavailable from exc
    finally:
        if root is not None:
            try:
                root.destroy()
            except (OSError, RuntimeError, tk.TclError):
                pass
    return _normalize_selected_path(selected)


def _pick_with_command(command: list[str]) -> str | None:
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise _DialogBackendUnavailable from exc
    if result.returncode == 1:
        return None
    if result.returncode != 0:
        raise _DialogBackendUnavailable(
            result.stderr.strip() or f"Dialog exited with code {result.returncode}."
        )
    return _normalize_selected_path(result.stdout)


def pick_directory_dialog(
    title: str = "Выберите рабочую папку ShortsFarm",
) -> str | None:
    """Open a local directory picker and return an absolute selected path."""
    if not _has_gui_session():
        raise LocalDialogUnavailable(UNAVAILABLE_MESSAGE)

    try:
        return _pick_with_tkinter(title)
    except _DialogBackendUnavailable:
        pass

    zenity = shutil.which("zenity")
    if zenity:
        try:
            return _pick_with_command([
                zenity,
                "--file-selection",
                "--directory",
                f"--title={title}",
            ])
        except _DialogBackendUnavailable:
            pass

    kdialog = shutil.which("kdialog")
    if kdialog:
        try:
            return _pick_with_command([
                kdialog,
                "--title",
                title,
                "--getexistingdirectory",
                str(Path.home()),
            ])
        except _DialogBackendUnavailable:
            pass

    raise LocalDialogUnavailable(UNAVAILABLE_MESSAGE)
