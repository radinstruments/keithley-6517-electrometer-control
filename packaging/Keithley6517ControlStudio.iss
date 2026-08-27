; Inno Setup installer for Keithley 6517 Control Studio

#define AppName "Keithley 6517 Control Studio"
#define AppVersion "1.0.0"
#define AppExeName "Keithley6517ControlStudio.exe"

[Setup]
AppId={{8F9D1E1A-4D8E-4F55-9E28-6517A9B4C001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=RADinstruments
DefaultDirName={localappdata}\Programs\Keithley6517ControlStudio
DefaultGroupName={#AppName}
DisableProgramGroupPage=no
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Keithley6517ControlStudio-Setup
SetupIconFile=..\assets\branding\keithley_6517_spectrum_icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "portuguese_brazilian"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos adicionais:"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{userdocs}\Keithley6517ControlStudio\data"; Flags: uninsneveruninstall
Name: "{userdocs}\Keithley6517ControlStudio\log"; Flags: uninsneveruninstall
Name: "{userdocs}\Keithley6517ControlStudio\config"; Flags: uninsneveruninstall

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
