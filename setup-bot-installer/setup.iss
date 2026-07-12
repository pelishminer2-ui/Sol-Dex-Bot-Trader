; Sol Dex Bot Trader — Inno Setup script
; Compile with: ISCC.exe setup.iss
; Prefer: .\build.ps1 from this folder (stamps build date/time automatically)
;
; Optional defines from build.ps1:
;   /DMyAppVersion=1.0.1
;   /DMyAppBuildDate=2026-07-12
;   /DMyAppBuildTime=13:45:00
;   /DMyAppBuildStamp=2026-07-12 13:45:00 -04:00

#ifndef MyAppVersion
  #define MyAppVersion "1.0.1"
#endif
#ifndef MyAppBuildDate
  #define MyAppBuildDate GetDateTimeString('yyyy-mm-dd', '-', ':')
#endif
#ifndef MyAppBuildTime
  #define MyAppBuildTime GetDateTimeString('hh:nn:ss', '-', ':')
#endif
#ifndef MyAppBuildStamp
  #define MyAppBuildStamp MyAppBuildDate + " " + MyAppBuildTime
#endif

#define MyAppName "Sol Dex Bot Trader"
#define MyAppPublisher "Sol Dex Bot Trader"
#define MyAppURL "http://127.0.0.1:5000"
#define MyAppExeName "SolDexBotTrader.exe"
#define MyAppCopyright "Copyright (C) 2026 Sol Dex Bot Trader"

[Setup]
AppId={{A7C3E9F1-8B2D-4E6A-9C1F-5D8B0A2E4F67}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion} ({#MyAppBuildDate})
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppCopyright={#MyAppCopyright}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName} {#MyAppVersion}
CloseApplications=yes
VersionInfoVersion={#MyAppVersion}.0
VersionInfoProductVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoTextVersion={#MyAppVersion} built {#MyAppBuildStamp}
VersionInfoProductName={#MyAppName}
VersionInfoProductTextVersion={#MyAppVersion} ({#MyAppBuildStamp})
VersionInfoCopyright={#MyAppCopyright}
VersionInfoOriginalFileName=setup.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "build\app\SolDexBotTrader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "Sol-Dex-Bot-Trader-User-Guide.pdf"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\.env.example"; DestDir: "{app}"; DestName: ".env.example"; Flags: ignoreversion
Source: "BUILD_INFO.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "version.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\User Guide (PDF)"; Filename: "{app}\docs\Sol-Dex-Bot-Trader-User-Guide.pdf"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
