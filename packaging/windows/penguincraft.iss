; Inno Setup — установщик PenguinCraft для Windows.
;
; Собирается скриптом packaging/windows/build.py (он передаёт параметры
; через /D, поэтому здесь нет зашитых путей и версий):
;
;   iscc /DMyAppVersion=1.3.0 /DAppSource=<...> /DOutputRoot=<...> \
;        /DIconFile=<...> penguincraft.iss
;
; Установка идёт в профиль пользователя (%LOCALAPPDATA%\Programs), поэтому
; права администратора не нужны.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef AppSource
  #define AppSource "..\..\build\windows\PenguinCraft"
#endif
#ifndef OutputRoot
  #define OutputRoot "..\..\dist"
#endif
#ifndef IconFile
  #define IconFile "penguincraft.ico"
#endif

[Setup]
; Уникальный идентификатор приложения — не менять между версиями,
; иначе обновление поставится рядом, а не поверх.
AppId={{7B1F4C2A-9E63-4D5B-8A17-2C6F0E9D4B31}
AppName=PenguinCraft
AppVersion={#MyAppVersion}
AppVerName=PenguinCraft {#MyAppVersion}
AppPublisher=PenguinCraft
AppPublisherURL=https://github.com/Volodymyr538/penguincraft
AppSupportURL=https://github.com/Volodymyr538/penguincraft/issues
AppUpdatesURL=https://github.com/Volodymyr538/penguincraft/releases
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription=Установщик лаунчера Minecraft PenguinCraft
VersionInfoProductName=PenguinCraft
DefaultDirName={localappdata}\Programs\PenguinCraft
DefaultGroupName=PenguinCraft
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
MinVersion=10.0
OutputDir={#OutputRoot}
OutputBaseFilename=PenguinCraft-{#MyAppVersion}-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\PenguinCraft.exe
SolidCompression=yes
Compression=lzma2/max
WizardStyle=modern
AllowNoIcons=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\PenguinCraft"; Filename: "{app}\PenguinCraft.exe"
Name: "{group}\{cm:UninstallProgram,PenguinCraft}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\PenguinCraft"; Filename: "{app}\PenguinCraft.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PenguinCraft.exe"; Description: "{cm:LaunchProgram,PenguinCraft}"; Flags: nowait postinstall skipifsilent
