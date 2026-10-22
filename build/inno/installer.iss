; Script Inno Setup — installateur Windows pour EduSync AD (build Go/Wails)
; Build local : iscc build/inno/installer.iss
; Build CI    : iscc /DMyAppVersion=0.1.0-alpha.1 build/inno/installer.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

#define MyAppName "EduSync AD"
#define MyAppPublisher "EduSync"
#define MyAppURL "https://github.com/estemobs/EduSync-AD"
#define MyAppExeName "edusync-ad.exe"

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
; SetupIconFile=..\..\assets\icon.ico  (optionnel, décommentez si icon.ico existe)
; WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=yes
DisableProgramGroupPage=yes
PrivilegesRequired=admin

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis additionnels :"; Flags: unchecked

[Files]
Source: "bin\edusync-ad.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\EduSync AD"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Désinstaller EduSync AD"; Filename: "{uninstallexe}"
Name: "{autodesktop}\EduSync AD"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Sans "skipifsilent" : se relance aussi après une mise à jour silencieuse déclenchée par l'appli elle-même.
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer EduSync AD"; Flags: nowait postinstall
