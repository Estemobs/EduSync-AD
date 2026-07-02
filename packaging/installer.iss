; Script Inno Setup — installateur Windows pour EduSync AD
; Build local : iscc packaging/installer.iss
; Build CI    : iscc /DMyAppVersion=1.2.3 packaging/installer.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

#define MyAppName "EduSync AD"
#define MyAppPublisher "EduSync"
#define MyAppURL "https://github.com/estemobs/EduSync-AD"
#define MyAppExeName "EduSyncAD.exe"

[Setup]
; GUID fixe : permet à Inno Setup de détecter une installation existante et de la mettre à jour proprement.
AppId={{2F6B8C36-9C0E-4C77-9B6B-6C4B7C0E5A11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\EduSyncAD
DefaultGroupName=EduSync AD
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=..
OutputBaseFilename=EduSyncAD-Setup
Compression=lzma2
SolidCompression=yes
SetupIconFile=..\assets\icon.ico
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=yes
DisableProgramGroupPage=yes

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis additionnels :"; Flags: unchecked

[Files]
Source: "..\dist\EduSyncAD\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\EduSync AD"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Désinstaller EduSync AD"; Filename: "{uninstallexe}"
Name: "{autodesktop}\EduSync AD"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Sans "skipifsilent" : se relance aussi après une mise à jour silencieuse déclenchée par l'appli elle-même.
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer EduSync AD"; Flags: nowait postinstall
