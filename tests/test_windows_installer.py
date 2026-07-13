from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER_DIR = ROOT / "installer" / "windows"


def test_windows_bootstrapper_installs_local_web_app_contract():
    script = (INSTALLER_DIR / "install.ps1").read_text(encoding="utf-8")

    assert "RepositoryZipUrl" in script
    assert "github.com/username8448/shortsfarm/archive/refs/heads/main.zip" in script
    assert "%LOCALAPPDATA%" not in script
    assert "$env:LOCALAPPDATA" in script
    assert '[string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "ShortsFarm")' in script
    assert '[string]$DataRoot = (Join-Path $env:LOCALAPPDATA "ShortsFarm\\data")' in script
    assert '$AppRoot = Join-Path $InstallRoot "app"' in script
    assert "SHORTSFARM_HOME" in script
    assert "SHORTSFARM_CHROMIUM" in script
    assert "web --host 127.0.0.1 --port `$Port --open-browser" in script


def test_windows_bootstrapper_installs_required_media_dependencies():
    script = (INSTALLER_DIR / "install.ps1").read_text(encoding="utf-8")

    assert "Python.Python.3.12" in script
    assert "OpenJS.NodeJS.LTS" in script
    assert "Gyan.FFmpeg" in script
    assert "shinchiro.mpv" in script
    assert "Google.Chrome" in script
    assert 'Arguments @("--prefix", "frontend", "install")' in script
    assert 'Arguments @("--prefix", "frontend", "run", "build")' in script
    assert 'Arguments @("-m", "pip", "install", "-e"' in script
    assert "shortsfarm.exe') doctor" in script


def test_windows_bootstrapper_creates_shortcuts_and_update_flow():
    script = (INSTALLER_DIR / "install.ps1").read_text(encoding="utf-8")

    assert "Start-ShortsFarm.ps1" in script
    assert "Stop-ShortsFarm.ps1" in script
    assert "Update-ShortsFarm.ps1" in script
    assert "ShortsFarm Doctor.lnk" in script
    assert "Microsoft\\Windows\\Start Menu\\Programs\\ShortsFarm" in script
    assert "Desktop" in script
    assert "Invoke-WebRequest -Uri \"`$url/api/doctor\"" in script


def test_inno_setup_wraps_bootstrapper_as_setup_exe():
    iss = (INSTALLER_DIR / "ShortsFarm.iss").read_text(encoding="utf-8")

    assert "OutputBaseFilename=ShortsFarmSetup" in iss
    assert "PrivilegesRequired=lowest" in iss
    assert "install.ps1" in iss
    assert "RepositoryZipUrl" in iss
    assert "runascurrentuser" in iss
    assert "Uninstallable=no" in iss


def test_windows_installer_docs_describe_paths_and_remotion_requirements():
    docs = (INSTALLER_DIR / "README.md").read_text(encoding="utf-8")

    assert "%LOCALAPPDATA%\\ShortsFarm\\app" in docs
    assert "%LOCALAPPDATA%\\ShortsFarm\\data" in docs
    assert "Remotion" in docs
    assert "FFmpeg" in docs
    assert "mpv" in docs
    assert "Chrome" in docs
    assert "iscc installer\\windows\\ShortsFarm.iss" in docs
