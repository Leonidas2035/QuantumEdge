; ============================================================
; AI Scalper Bot Installer — OFFLINE version (Clean)
; ============================================================

[Setup]
AppName=AI Scalper Bot
AppVersion=1.0
DefaultDirName=C:\ai_scalper_bot
DisableDirPage=yes
DisableProgramGroupPage=yes
OutputBaseFilename=ai_scalper_bot_installer
Compression=lzma
SolidCompression=yes
PrivilegesRequired=admin
Uninstallable=no

; ----- ICONS -----
; IMPORTANT: Must exist NEXT TO this .iss file at compile time.
SetupIconFile=bot_icon.ico
WizardSmallImageFile=bot_icon.ico
WizardImageFile=bot_icon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; === Основні папки ===
Source: "bot\*"; DestDir: "{app}\bot"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "config\*"; DestDir: "{app}\config"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "data\*"; DestDir: "{app}\data"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "storage\*"; DestDir: "{app}\storage"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "Documentation\*"; DestDir: "{app}\Documentation"; Flags: recursesubdirs createallsubdirs ignoreversion

; === Файли ===
Source: "installer.ps1"; DestDir: "{app}"
Source: "requirements.txt"; DestDir: "{app}"
Source: "run_bot.exe"; DestDir: "{app}"
Source: "run_bot.cmd"; DestDir: "{app}"
Source: "bot_icon.ico"; DestDir: "{app}"

; === Python Offline Installer ===
Source: "python-3.12.10-amd64.exe"; DestDir: "{app}"

; НЕ включаємо dist, venv, build — вони генеруються автоматично.

[Run]
Filename: "powershell.exe"; \
Parameters: "-ExecutionPolicy Bypass -File ""{app}\installer.ps1"" -InstallDir ""{app}"""; \
Flags: runhidden waituntilterminated
