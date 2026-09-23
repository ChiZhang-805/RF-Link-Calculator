#ifndef AppVersion
  #define AppVersion "2.3.0"
#endif
[Setup]
AppId={{6A0B0E69-43E6-4F72-A195-FA446F215A0E}
AppName=RF Link
AppVersion={#AppVersion}
AppPublisher=ChiZhang-805
DefaultDirName={localappdata}\Programs\RF Link
DefaultGroupName=RF Link
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=RF-Link-Setup-{#AppVersion}-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\RF-Link.exe
CloseApplications=yes
[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
[CustomMessages]
chinesesimplified.DesktopShortcut=创建桌面快捷方式
chinesesimplified.LaunchApp=启动 RF Link
english.DesktopShortcut=Create a desktop shortcut
english.LaunchApp=Launch RF Link
[Files]
Source: "dist\RF-Link\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\RF Link"; Filename: "{app}\RF-Link.exe"
Name: "{autodesktop}\RF Link"; Filename: "{app}\RF-Link.exe"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopShortcut}"; Flags: unchecked
[Run]
Filename: "{app}\RF-Link.exe"; Description: "{cm:LaunchApp}"; Flags: nowait postinstall skipifsilent
