; BootForge Windows NSIS Installer Script
; Produces release/BootForge-${VERSION}-win64-setup.exe

!ifndef VERSION
  !define VERSION "1.0.1"
!endif

Unicode True
ManifestDPIAware True
RequestExecutionLevel admin

!define PRODUCT_NAME "BootForge"
!define PRODUCT_PUBLISHER "NiceMank"
!define PRODUCT_WEB_SITE "https://github.com/NiceMank/BootForge"
!define PRODUCT_DIR_REGKEY "Software\Microsoft\Windows\CurrentVersion\App Paths\BootForge.exe"
!define PRODUCT_UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"
!define PRODUCT_UNINST_ROOT_KEY "HKLM"

SetCompressor /SOLID lzma

Name "${PRODUCT_NAME} ${VERSION}"
OutFile "..\release\BootForge-${VERSION}-win64-setup.exe"
InstallDir "$PROGRAMFILES64\BootForge"
InstallDirRegKey HKLM "${PRODUCT_DIR_REGKEY}" ""
ShowInstDetails show
ShowUnInstDetails show

Icon "..\assets\icon.ico"
UninstallIcon "..\assets\icon.ico"

!include "MUI2.nsh"

; MUI Settings
!define MUI_ABORTWARNING
!define MUI_ICON "..\assets\icon.ico"
!define MUI_UNICON "..\assets\icon.ico"

; Pages
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\BootForge.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Lancer BootForge maintenant"
!insertmacro MUI_PAGE_FINISH

; Uninstaller pages
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; Language
!insertmacro MUI_LANGUAGE "French"
!insertmacro MUI_LANGUAGE "English"

Section "MainSection" SEC01
  SetOutPath "$INSTDIR"
  SetOverwrite ifnewer
  File /r "..\dist\BootForge\*.*"

  CreateDirectory "$SMPROGRAMS\BootForge"
  CreateShortcut "$SMPROGRAMS\BootForge\BootForge.lnk" "$INSTDIR\BootForge.exe" "" "$INSTDIR\assets\icon.ico"
  CreateShortcut "$SMPROGRAMS\BootForge\Désinstaller BootForge.lnk" "$INSTDIR\uninstall.exe"
  CreateShortcut "$DESKTOP\BootForge.lnk" "$INSTDIR\BootForge.exe" "" "$INSTDIR\assets\icon.ico"
SectionEnd

Section -AdditionalIcons
  WriteIniStr "$INSTDIR\${PRODUCT_NAME}.url" "InternetShortcut" "URL" "${PRODUCT_WEB_SITE}"
  CreateShortcut "$SMPROGRAMS\BootForge\Site web BootForge.lnk" "$INSTDIR\${PRODUCT_NAME}.url"
SectionEnd

Section -Post
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "${PRODUCT_DIR_REGKEY}" "" "$INSTDIR\BootForge.exe"
  WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "DisplayName" "$(^Name)"
  WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "UninstallString" "$INSTDIR\uninstall.exe"
  WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "DisplayIcon" "$INSTDIR\BootForge.exe"
  WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "URLInfoAbout" "${PRODUCT_WEB_SITE}"
  WriteRegStr ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
SectionEnd

Function un.onUninstSuccess
  HideWindow
  MessageBox MB_ICONINFORMATION|MB_OK "$(^Name) a été désinstallé avec succès."
FunctionEnd

Function un.onInit
  MessageBox MB_ICONQUESTION|MB_YESNO|MB_DEFBUTTON2 "Êtes-vous certain de vouloir désinstaller complètement $(^Name) ?" IDYES +2
  Abort
FunctionEnd

Section Uninstall
  Delete "$DESKTOP\BootForge.lnk"
  Delete "$SMPROGRAMS\BootForge\*.lnk"
  RMDir "$SMPROGRAMS\BootForge"

  RMDir /r "$INSTDIR"

  DeleteRegKey ${PRODUCT_UNINST_ROOT_KEY} "${PRODUCT_UNINST_KEY}"
  DeleteRegKey HKLM "${PRODUCT_DIR_REGKEY}"
  SetAutoClose true
SectionEnd
