; =============================================================================
;  Metavoid — Inno Setup installer script (Windows x64)
;  Produces:  dist\installer\Metavoid-Setup-<version>.exe
;
;  Build order (see scripts\build_installer.bat):
;    1) run scripts\build.bat            -> dist\Metavoid.exe
;    2) compile this script with ISCC    -> installer .exe
;
;  The result installs a REAL Windows application: Start Menu folder, optional
;  desktop icon, Add/Remove Programs entry and a full uninstaller.
; =============================================================================

#define MyAppName "Metavoid"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "Metavoid"
#define MyAppExeName "Metavoid.exe"

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
OutputBaseFilename=Metavoid-Setup-{#MyAppVersion}
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

[InstallDelete]
; Rebrand: 1.0.0 shipped as "AI Metadata Remover". The AppId is unchanged so
; this upgrades that install in place rather than adding a second entry to
; Add/Remove Programs — but the old executable and shortcuts would otherwise
; be left behind under the previous name, so remove them first.
Type: files; Name: "{app}\AI-Metadata-Remover.exe"
Type: filesandordirs; Name: "{autoprograms}\AI Metadata Remover"
Type: files; Name: "{autodesktop}\AI Metadata Remover.lnk"

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
