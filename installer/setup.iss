; installer/setup.iss
; Build with Inno Setup (https://jrsoftware.org/isinfo.php) after producing
; dist\PBIXLineageTool\ via:  pyinstaller PBIXLineageTool.spec
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\setup.iss
;
; Produces installer\output\PBIXLineageToolSetup.exe - a single file a user
; double-clicks to install, with no other prompts required.

#define MyAppName "PBIX Lineage Tool"
#define MyAppVersion GetFileVersion("..\dist\PBIXLineageTool\PBIXLineageTool.exe")
#define MyAppPublisher "Your Organization"
#define MyAppExeName "PBIXLineageTool.exe"

[Setup]
; Keep this GUID fixed across all versions - it's what lets a re-run of the
; installer upgrade the existing install in place instead of creating a
; duplicate entry.
AppId={{B7B8B6E4-6C1E-4C6C-9C7F-1B7C2B7C9F10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=PBIXLineageToolSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "..\dist\PBIXLineageTool\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
