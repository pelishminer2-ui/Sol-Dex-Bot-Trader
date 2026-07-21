; Sol Dex Bot Trader — Inno Setup script
; Compile with: ISCC.exe setup.iss
; Prefer: .\build.ps1 from this folder (stamps build date/time automatically)
;
; Optional defines from build.ps1 (values stamped at compile time):
;   /DMyAppVersion=1.1.6
;   /DMyAppBuildDate=yyyy-mm-dd
;   /DMyAppBuildTime=HH:mm:ss
;   /DMyAppBuildStamp=yyyy-mm-ddTHH:mm:sszzz
;
; Icons (see assets/ICON_ASSIGNMENT.txt):
;   Taskbar/app/Setup: assets\icon-taskbar-cats.ico   (Cats of Crypto)
;   Desktop shortcut:  assets\icon-desktop-pelish.ico (Pelish Crypto medallion)

#ifndef MyAppVersion
  #define MyAppVersion "1.1.6"
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
#define MyAppTaskbarIcon "assets\icon-taskbar-cats.ico"
#define MyAppDesktopIcon "assets\icon-desktop-pelish.ico"

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
SetupIconFile={#MyAppTaskbarIcon}
UninstallDisplayIcon={app}\{#MyAppExeName}
; Close the running bot before file removal (Add/Remove Programs)
CloseApplications=yes
CloseApplicationsFilter=SolDexBotTrader.exe
RestartApplications=no
VersionInfoVersion={#MyAppVersion}.0
VersionInfoProductVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoTextVersion={#MyAppVersion}
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
Source: "Stop-SolDexBot.bat"; DestDir: "{app}"; Flags: ignoreversion
; Desktop shortcut uses a separate ICO (Pelish); app/taskbar/Start Menu use Cats via the exe.
Source: "{#MyAppDesktopIcon}"; DestDir: "{app}"; DestName: "icon-desktop-pelish.ico"; Flags: ignoreversion
Source: "{#MyAppTaskbarIcon}"; DestDir: "{app}"; DestName: "icon-taskbar-cats.ico"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\icon-taskbar-cats.ico"
Name: "{group}\Stop Sol Dex Bot Trader"; Filename: "{app}\Stop-SolDexBot.bat"; WorkingDir: "{app}"
Name: "{group}\User Guide (PDF)"; Filename: "{app}\docs\Sol-Dex-Bot-Trader-User-Guide.pdf"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\icon-desktop-pelish.ico"; Tasks: desktopicon

; Finish-page checkbox (unchecked by default) — user may optionally launch after install.
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent unchecked

; Force-stop before Inno deletes files (covers tray / locked DLLs CloseApplications may miss)
[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM SolDexBotTrader.exe /F /T >nul 2>&1 & ping -n 2 127.0.0.1 >nul"; RunOnceId: "StopSolDexBotTrader"; Flags: runhidden waituntilterminated

; Runtime leftovers under {app} (logs, .env, state JSON, data\, presets copies, etc.)
; Packaged bot writes only under the install directory (next to the exe) — not a separate AppData product tree.
; Icons / Start Menu group / uninstall registry key are removed by Inno automatically.
[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\presets"
Type: filesandordirs; Name: "{app}\docs"
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\*.log"
Type: files; Name: "{app}\*.json"
Type: files; Name: "{app}\*.jsonl"
Type: files; Name: "{app}\.env"
Type: files; Name: "{app}\.env.*"
Type: files; Name: "{app}\BUILD_INFO.txt"
Type: files; Name: "{app}\version.txt"
Type: files; Name: "{app}\Stop-SolDexBot.bat"
Type: files; Name: "{app}\{#MyAppExeName}"
Type: dirifempty; Name: "{app}"
; Final sweep: any remaining files/dirs left under {app} after the above
Type: filesandordirs; Name: "{app}"
