; ShortsFarm online Windows installer wrapper.
; Build with Inno Setup 6 on Windows:
;   iscc installer\windows\ShortsFarm.iss

#define MyAppName "ShortsFarm"
#define MyAppVersion "0.2.0"
#define RepoZipUrl "https://github.com/username8448/shortsfarm/archive/refs/heads/main.zip"

[Setup]
AppId={{9F14B6E4-FB3C-46F9-9A9C-31AC770C4C69}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=ShortsFarm
DefaultDirName={localappdata}\ShortsFarm\installer
DisableDirPage=yes
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=ShortsFarmSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Uninstallable=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "install.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install.ps1"" -RepositoryZipUrl ""{#RepoZipUrl}"""; \
  Description: "Install ShortsFarm and required media dependencies"; \
  Flags: postinstall waituntilterminated runascurrentuser
