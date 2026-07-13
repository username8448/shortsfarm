#requires -Version 5.1
<#
ShortsFarm Windows online bootstrapper.

This script installs ShortsFarm for the current Windows user:
- downloads the project ZIP from GitHub;
- installs/checks Python, Node.js, FFmpeg, mpv and Chrome through winget where possible;
- creates a Python venv and installs ShortsFarm;
- installs frontend/Remotion npm dependencies and builds the web UI;
- creates Start Menu/Desktop shortcuts that launch the local web app in a browser.
#>

[CmdletBinding()]
param(
    [string]$RepositoryZipUrl = "https://github.com/username8448/shortsfarm/archive/refs/heads/main.zip",
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "ShortsFarm"),
    [string]$DataRoot = (Join-Path $env:LOCALAPPDATA "ShortsFarm\data"),
    [int]$Port = 8000,
    [string]$PythonVersion = "3.12.8",
    [string]$NodeMsiUrl = "https://nodejs.org/dist/v22.11.0/node-v22.11.0-x64.msi",
    [switch]$NoShortcuts,
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$AppRoot = Join-Path $InstallRoot "app"
$CacheRoot = Join-Path $InstallRoot "cache"
$BinRoot = Join-Path $InstallRoot "bin"
$InstallerRoot = Join-Path $InstallRoot "installer"
$VenvPython = Join-Path $AppRoot ".venv\Scripts\python.exe"
$ShortsFarmExe = Join-Path $AppRoot ".venv\Scripts\shortsfarm.exe"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "OK  $Message" -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Host "WARN $Message" -ForegroundColor Yellow
}

function Invoke-External {
    param(
        [Parameter(Mandatory=$true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = ""
    )
    $argLine = $Arguments -join " "
    Write-Host "    $FilePath $argLine"
    $startInfo = @{
        FilePath = $FilePath
        ArgumentList = $Arguments
        Wait = $true
        PassThru = $true
        NoNewWindow = $true
    }
    if ($WorkingDirectory) {
        $startInfo.WorkingDirectory = $WorkingDirectory
    }
    $process = Start-Process @startInfo
    if ($process.ExitCode -ne 0) {
        throw "Command failed with exit code $($process.ExitCode): $FilePath $argLine"
    }
}

function Update-SessionPath {
    $paths = @(
        [Environment]::GetEnvironmentVariable("Path", "Machine"),
        [Environment]::GetEnvironmentVariable("Path", "User"),
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"),
        (Join-Path $env:ProgramFiles "nodejs"),
        (Join-Path ${env:ProgramFiles(x86)} "nodejs")
    ) | Where-Object { $_ }
    $env:Path = ($paths -join ";") + ";" + $env:Path
}

function Get-CommandSource([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    return $null
}

function Get-PythonPath {
    $py = Get-CommandSource "py.exe"
    if ($py) {
        $out = & $py -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) {
            return ($out | Select-Object -First 1).Trim()
        }
        $out = & $py -3 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) {
            return ($out | Select-Object -First 1).Trim()
        }
    }
    foreach ($candidate in @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        (Join-Path $env:ProgramFiles "Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python311\python.exe")
    )) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }
    $python = Get-CommandSource "python.exe"
    if ($python) {
        $ok = & $python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return $python
        }
    }
    return $null
}

function Install-WingetPackage {
    param(
        [Parameter(Mandatory=$true)][string]$Id,
        [Parameter(Mandatory=$true)][string]$Label
    )
    $winget = Get-CommandSource "winget.exe"
    if (-not $winget) {
        Write-Warn "winget not found; cannot install $Label automatically through winget."
        return $false
    }
    Write-Step "Installing/checking $Label through winget"
    $args = @(
        "install",
        "--id", $Id,
        "--silent",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--disable-interactivity"
    )
    $process = Start-Process -FilePath $winget -ArgumentList $args -Wait -PassThru -NoNewWindow
    Update-SessionPath
    if ($process.ExitCode -eq 0) {
        Write-Ok "$Label installed or already available."
        return $true
    }
    Write-Warn "winget returned exit code $($process.ExitCode) for $Label."
    return $false
}

function Invoke-Download {
    param(
        [Parameter(Mandatory=$true)][string]$Uri,
        [Parameter(Mandatory=$true)][string]$OutFile
    )
    Write-Host "    Download: $Uri"
    Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing
}

function Ensure-Python {
    Update-SessionPath
    $python = Get-PythonPath
    if ($python) {
        Write-Ok "Python found: $python"
        return $python
    }
    if (-not $SkipDependencyInstall) {
        Install-WingetPackage -Id "Python.Python.3.12" -Label "Python 3.12" | Out-Null
        $python = Get-PythonPath
        if ($python) {
            Write-Ok "Python found: $python"
            return $python
        }

        Write-Step "Downloading Python $PythonVersion installer"
        $installer = Join-Path $CacheRoot "python-$PythonVersion-amd64.exe"
        Invoke-Download -Uri "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe" -OutFile $installer
        Invoke-External -FilePath $installer -Arguments @(
            "/quiet",
            "InstallAllUsers=0",
            "PrependPath=1",
            "Include_launcher=1",
            "Include_pip=1"
        )
        Update-SessionPath
        $python = Get-PythonPath
    }
    if (-not $python) {
        throw "Python 3.11+ was not found. Install Python 3.12 and rerun this installer."
    }
    Write-Ok "Python found: $python"
    return $python
}

function Ensure-Node {
    Update-SessionPath
    $node = Get-CommandSource "node.exe"
    $npm = Get-CommandSource "npm.cmd"
    if ($node -and $npm) {
        Write-Ok "Node.js found: $node"
        return
    }
    if (-not $SkipDependencyInstall) {
        Install-WingetPackage -Id "OpenJS.NodeJS.LTS" -Label "Node.js LTS" | Out-Null
        Update-SessionPath
        $node = Get-CommandSource "node.exe"
        $npm = Get-CommandSource "npm.cmd"
        if ($node -and $npm) {
            Write-Ok "Node.js found: $node"
            return
        }

        Write-Step "Downloading Node.js MSI"
        $msi = Join-Path $CacheRoot "node-lts-x64.msi"
        Invoke-Download -Uri $NodeMsiUrl -OutFile $msi
        Invoke-External -FilePath "msiexec.exe" -Arguments @("/i", "`"$msi`"", "/qn", "/norestart")
        Update-SessionPath
    }
    if (-not (Get-CommandSource "node.exe") -or -not (Get-CommandSource "npm.cmd")) {
        throw "Node.js/npm was not found. Install Node.js LTS and rerun this installer."
    }
    Write-Ok "Node.js and npm are available."
}

function Ensure-WingetBinary {
    param(
        [Parameter(Mandatory=$true)][string]$CommandName,
        [Parameter(Mandatory=$true)][string]$PackageId,
        [Parameter(Mandatory=$true)][string]$Label
    )
    Update-SessionPath
    $path = Get-CommandSource $CommandName
    if ($path) {
        Write-Ok "$Label found: $path"
        return $path
    }
    if (-not $SkipDependencyInstall) {
        Install-WingetPackage -Id $PackageId -Label $Label | Out-Null
        Update-SessionPath
        $path = Get-CommandSource $CommandName
    }
    if (-not $path) {
        throw "$Label was not found. Install package '$PackageId' with winget and rerun this installer."
    }
    Write-Ok "$Label found: $path"
    return $path
}

function Get-ChromePath {
    foreach ($candidate in @(
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
    )) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }
    foreach ($name in @("chrome.exe", "google-chrome.exe", "chromium.exe")) {
        $path = Get-CommandSource $name
        if ($path) {
            return $path
        }
    }
    return $null
}

function Ensure-Chrome {
    $chrome = Get-ChromePath
    if ($chrome) {
        Write-Ok "Chrome/Chromium found: $chrome"
        return $chrome
    }
    if (-not $SkipDependencyInstall) {
        Install-WingetPackage -Id "Google.Chrome" -Label "Google Chrome for Remotion Chromium" | Out-Null
        Update-SessionPath
        $chrome = Get-ChromePath
    }
    if (-not $chrome) {
        throw "Chrome/Chromium was not found. Install Google Chrome or set SHORTSFARM_CHROMIUM."
    }
    Write-Ok "Chrome/Chromium found: $chrome"
    return $chrome
}

function ConvertTo-PSLiteral([string]$Value) {
    return "'" + ($Value -replace "'", "''") + "'"
}

function New-Shortcut {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$TargetPath,
        [string]$Arguments = "",
        [string]$WorkingDirectory = "",
        [string]$Description = "ShortsFarm"
    )
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $TargetPath
    $shortcut.Arguments = $Arguments
    if ($WorkingDirectory) {
        $shortcut.WorkingDirectory = $WorkingDirectory
    }
    $shortcut.Description = $Description
    $shortcut.Save()
}

function Write-LauncherScripts {
    param([string]$ChromePath)

    New-Item -ItemType Directory -Force -Path $BinRoot, $InstallerRoot | Out-Null
    if ($PSCommandPath) {
        Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $InstallerRoot "install.ps1") -Force
    }

    $appLiteral = ConvertTo-PSLiteral $AppRoot
    $dataLiteral = ConvertTo-PSLiteral $DataRoot
    $chromeLiteral = ConvertTo-PSLiteral $ChromePath
    $repoLiteral = ConvertTo-PSLiteral $RepositoryZipUrl
    $installLiteral = ConvertTo-PSLiteral $InstallRoot
    $startScript = @"
`$ErrorActionPreference = 'Stop'
`$AppRoot = $appLiteral
`$DataRoot = $dataLiteral
`$Port = $Port
`$env:SHORTSFARM_HOME = `$DataRoot
[Environment]::SetEnvironmentVariable('SHORTSFARM_HOME', `$DataRoot, 'Process')
`$chrome = $chromeLiteral
if (`$chrome -and (Test-Path `$chrome)) {
    `$env:SHORTSFARM_CHROMIUM = `$chrome
}
`$env:Path = (Join-Path `$env:LOCALAPPDATA 'Microsoft\WinGet\Links') + ';' + (Join-Path `$env:ProgramFiles 'nodejs') + ';' + `$env:Path
`$url = "http://127.0.0.1:`$Port"
try {
    `$response = Invoke-WebRequest -Uri "`$url/api/doctor" -TimeoutSec 2 -UseBasicParsing
    if (`$response.StatusCode -lt 500) {
        Start-Process `$url
        exit 0
    }
} catch {}
Set-Location `$AppRoot
& (Join-Path `$AppRoot '.venv\Scripts\shortsfarm.exe') web --host 127.0.0.1 --port `$Port --open-browser
"@
    Set-Content -LiteralPath (Join-Path $BinRoot "Start-ShortsFarm.ps1") -Value $startScript -Encoding UTF8

    $stopScript = @"
`$ErrorActionPreference = 'Stop'
`$AppRoot = $appLiteral
`$DataRoot = $dataLiteral
`$env:SHORTSFARM_HOME = `$DataRoot
Set-Location `$AppRoot
& (Join-Path `$AppRoot '.venv\Scripts\shortsfarm.exe') stop
"@
    Set-Content -LiteralPath (Join-Path $BinRoot "Stop-ShortsFarm.ps1") -Value $stopScript -Encoding UTF8

    $updateScript = @"
`$ErrorActionPreference = 'Stop'
`$InstallRoot = $installLiteral
`$installer = Join-Path `$InstallRoot 'installer\install.ps1'
if (-not (Test-Path `$installer)) {
    throw "Installer script was not found: `$installer"
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File `$installer -RepositoryZipUrl $repoLiteral -InstallRoot `$InstallRoot -DataRoot $dataLiteral -Port $Port
"@
    Set-Content -LiteralPath (Join-Path $BinRoot "Update-ShortsFarm.ps1") -Value $updateScript -Encoding UTF8

    $doctorScript = @"
`$ErrorActionPreference = 'Stop'
`$AppRoot = $appLiteral
`$DataRoot = $dataLiteral
`$env:SHORTSFARM_HOME = `$DataRoot
`$chrome = $chromeLiteral
if (`$chrome -and (Test-Path `$chrome)) {
    `$env:SHORTSFARM_CHROMIUM = `$chrome
}
Set-Location `$AppRoot
& (Join-Path `$AppRoot '.venv\Scripts\shortsfarm.exe') doctor
Read-Host 'Press Enter to close'
"@
    Set-Content -LiteralPath (Join-Path $BinRoot "ShortsFarm-Doctor.ps1") -Value $doctorScript -Encoding UTF8
}

function Install-Shortcuts {
    $programsDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\ShortsFarm"
    $desktopDir = [Environment]::GetFolderPath("Desktop")
    New-Item -ItemType Directory -Force -Path $programsDir | Out-Null
    $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $startFile = Join-Path $BinRoot "Start-ShortsFarm.ps1"
    $stopFile = Join-Path $BinRoot "Stop-ShortsFarm.ps1"
    $updateFile = Join-Path $BinRoot "Update-ShortsFarm.ps1"
    $doctorFile = Join-Path $BinRoot "ShortsFarm-Doctor.ps1"

    New-Shortcut -Path (Join-Path $programsDir "ShortsFarm.lnk") -TargetPath $powershell -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$startFile`"" -WorkingDirectory $AppRoot -Description "Start ShortsFarm"
    New-Shortcut -Path (Join-Path $programsDir "Stop ShortsFarm.lnk") -TargetPath $powershell -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$stopFile`"" -WorkingDirectory $AppRoot -Description "Stop ShortsFarm"
    New-Shortcut -Path (Join-Path $programsDir "Update ShortsFarm.lnk") -TargetPath $powershell -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$updateFile`"" -WorkingDirectory $InstallRoot -Description "Update ShortsFarm"
    New-Shortcut -Path (Join-Path $programsDir "ShortsFarm Doctor.lnk") -TargetPath $powershell -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$doctorFile`"" -WorkingDirectory $AppRoot -Description "Check ShortsFarm dependencies"
    New-Shortcut -Path (Join-Path $desktopDir "ShortsFarm.lnk") -TargetPath $powershell -Arguments "-NoProfile -ExecutionPolicy Bypass -File `"$startFile`"" -WorkingDirectory $AppRoot -Description "Start ShortsFarm"
}

function Install-SourceFromZip {
    Write-Step "Downloading ShortsFarm source ZIP"
    $zipPath = Join-Path $CacheRoot "shortsfarm-source.zip"
    $extractRoot = Join-Path $CacheRoot "source"
    if (Test-Path $extractRoot) {
        Remove-Item -LiteralPath $extractRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $extractRoot | Out-Null
    Invoke-Download -Uri $RepositoryZipUrl -OutFile $zipPath
    Expand-Archive -LiteralPath $zipPath -DestinationPath $extractRoot -Force
    $sourceRoot = Get-ChildItem -LiteralPath $extractRoot -Directory | Select-Object -First 1
    if (-not $sourceRoot) {
        throw "Downloaded ZIP does not contain a project directory."
    }

    $backupRoot = $null
    if (Test-Path $AppRoot) {
        $backupRoot = Join-Path $InstallRoot ("app.backup-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
        Move-Item -LiteralPath $AppRoot -Destination $backupRoot -Force
    }
    try {
        New-Item -ItemType Directory -Force -Path $AppRoot | Out-Null
        Get-ChildItem -LiteralPath $sourceRoot.FullName -Force | Copy-Item -Destination $AppRoot -Recurse -Force
        if ($backupRoot -and (Test-Path $backupRoot)) {
            Remove-Item -LiteralPath $backupRoot -Recurse -Force
        }
    } catch {
        if ($backupRoot -and (Test-Path $backupRoot)) {
            if (Test-Path $AppRoot) {
                Remove-Item -LiteralPath $AppRoot -Recurse -Force
            }
            Move-Item -LiteralPath $backupRoot -Destination $AppRoot -Force
        }
        throw
    }
}

Write-Step "Preparing ShortsFarm directories"
New-Item -ItemType Directory -Force -Path $InstallRoot, $DataRoot, $CacheRoot, $BinRoot, $InstallerRoot | Out-Null

Write-Step "Checking Windows dependencies"
$python = Ensure-Python
Ensure-Node
Ensure-WingetBinary -CommandName "ffmpeg.exe" -PackageId "Gyan.FFmpeg" -Label "FFmpeg" | Out-Null
Ensure-WingetBinary -CommandName "ffprobe.exe" -PackageId "Gyan.FFmpeg" -Label "FFprobe" | Out-Null
Ensure-WingetBinary -CommandName "mpv.exe" -PackageId "shinchiro.mpv" -Label "mpv" | Out-Null
$chrome = Ensure-Chrome
[Environment]::SetEnvironmentVariable("SHORTSFARM_HOME", $DataRoot, "User")
[Environment]::SetEnvironmentVariable("SHORTSFARM_CHROMIUM", $chrome, "User")

Install-SourceFromZip

Write-Step "Creating Python virtual environment"
Invoke-External -FilePath $python -Arguments @("-m", "venv", "`"$AppRoot\.venv`"")
Invoke-External -FilePath $VenvPython -Arguments @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel")
Invoke-External -FilePath $VenvPython -Arguments @("-m", "pip", "install", "-e", "`"$AppRoot`"")

Write-Step "Installing frontend and Remotion dependencies"
$npm = Get-CommandSource "npm.cmd"
if (-not $npm) {
    throw "npm was not found after Node.js installation."
}
Invoke-External -FilePath $npm -Arguments @("--prefix", "frontend", "install") -WorkingDirectory $AppRoot
Invoke-External -FilePath $npm -Arguments @("--prefix", "frontend", "run", "build") -WorkingDirectory $AppRoot

Write-Step "Initializing ShortsFarm data and database"
$env:SHORTSFARM_HOME = $DataRoot
$env:SHORTSFARM_CHROMIUM = $chrome
Invoke-External -FilePath $ShortsFarmExe -Arguments @("init") -WorkingDirectory $AppRoot
Invoke-External -FilePath $ShortsFarmExe -Arguments @("doctor") -WorkingDirectory $AppRoot

Write-Step "Writing launch/update scripts"
Write-LauncherScripts -ChromePath $chrome

if (-not $NoShortcuts) {
    Write-Step "Creating Windows shortcuts"
    Install-Shortcuts
}

Write-Host ""
Write-Host "ShortsFarm is installed." -ForegroundColor Green
Write-Host "Start Menu: ShortsFarm -> ShortsFarm"
Write-Host "Data: $DataRoot"
Write-Host "App:  $AppRoot"
Write-Host "URL:  http://127.0.0.1:$Port"
