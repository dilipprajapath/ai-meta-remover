; =============================================================================
;  AI Metadata Remover — Inno Setup installer script (Windows x64)
;  Produces:  dist\installer\AI-Metadata-Remover-Setup-1.0.0.exe
;
;  Build order (see scripts\build_installer.bat):
;    1) run scripts\build.bat            -> dist\AI-Metadata-Remover.exe
;    2) compile this script with ISCC    -> installer .exe
;
;  The result installs a REAL Windows application: Start Menu folder, optional
;  desktop icon, Add/Remove Programs entry and a full uninstaller.
; =============================================================================

#define MyAppName "AI Metadata Remover"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "AI Metadata Remover"
#define MyAppExeName "AI-Metadata-Remover.exe"

[Setup]
AppId={{8A7F2C14-9E03-4C6A-9D2F-1F11C6D9A6B1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Strip AI / C2PA metadata from images
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=..\dist\installer
OutputBaseFilename=AI-Metadata-Remover-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
DisableProgramGroupPage=no
CloseApplications=yes
UninstallDisplayName={#MyAppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
