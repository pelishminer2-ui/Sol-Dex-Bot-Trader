; Sol Dex Bot Trader — Inno Setup script
; Compile with: ISCC.exe setup.iss
; Prefer: .\build.ps1 from this folder

#define MyAppName "Sol Dex Bot Trader"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Sol Dex Bot Trader"
#define MyAppURL "http://127.0.0.1:5000"
#define MyAppExeName "SolDexBotTrader.exe"

[Setup]
AppId={{A7C3E9F1-8B2D-4E6A-9C1F-5D8B0A2E4F67}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
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
UninstallDisplayName={#MyAppName}
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "build\app\SolDexBotTrader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "Sol-Dex-Bot-Trader-User-Guide.pdf"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\.env.example"; DestDir: "{app}"; DestName: ".env.example"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\User Guide (PDF)"; Filename: "{app}\docs\Sol-Dex-Bot-Trader-User-Guide.pdf"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
