; Ghost Coder installer (per-user, no admin rights required).
; Build via packaging/build.ps1, or:
;   makensis /DVERSION=x.y.z /DSOURCE_DIR=build\nuitka\GhostCoder /DOUT_FILE=dist\GhostCoder-Setup-x.y.z.exe packaging\installer.nsi

Unicode true
!include "MUI2.nsh"

!ifndef VERSION
  !error "VERSION not defined"
!endif
!ifndef SOURCE_DIR
  !error "SOURCE_DIR not defined"
!endif
!ifndef OUT_FILE
  !define OUT_FILE "GhostCoder-Setup-${VERSION}.exe"
!endif

!define APP_NAME "Ghost Coder"
!define APP_EXE "GhostCoder.exe"
!define PUBLISHER "Seth Stenzel"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\GhostCoder"

Name "${APP_NAME} ${VERSION}"
OutFile "${OUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\${APP_NAME}"
InstallDirRegKey HKCU "Software\GhostCoder" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ManifestDPIAware true

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APP_NAME}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "FileDescription" "${APP_NAME} Installer"
VIAddVersionKey "CompanyName" "${PUBLISHER}"
VIAddVersionKey "LegalCopyright" "MIT License, ${PUBLISHER}"

!define MUI_ICON "${__FILEDIR__}\..\src\ghost_coder\assets\icon.ico"
!define MUI_UNICON "${__FILEDIR__}\..\src\ghost_coder\assets\icon.ico"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Launch ${APP_NAME}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${__FILEDIR__}\..\LICENSE"
!insertmacro MUI_PAGE_COMPONENTS
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE EnsureAppSubdir
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; The install folder is wiped on upgrade/uninstall, so always use a dedicated "Ghost Coder"
; folder, never a folder the user picked directly (e.g. Documents).
Function EnsureAppSubdir
  StrCpy $0 "$INSTDIR" "" -11
  StrCmp $0 "${APP_NAME}" +2
  StrCpy $INSTDIR "$INSTDIR\${APP_NAME}"
FunctionEnd

Section "!${APP_NAME}" SecApp
  SectionIn RO
  ; Close a running instance so its files can be replaced.
  nsExec::Exec 'taskkill /IM "${APP_EXE}" /T /F'
  Sleep 500

  Call EnsureAppSubdir
  ; Remove files from a previous version, then copy the new build.
  RMDir /r "$INSTDIR"
  SetOutPath "$INSTDIR"
  File /r "${SOURCE_DIR}\*.*"
  File "/oname=LICENSE.txt" "${__FILEDIR__}\..\LICENSE"

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"

  WriteRegStr HKCU "Software\GhostCoder" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${PUBLISHER}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "URLInfoAbout" "https://github.com/sethstenzel/ghost-coder"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "Desktop shortcut" SecDesktop
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecApp} "The ${APP_NAME} application (required)."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "Add a shortcut to the desktop."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Section "Uninstall"
  nsExec::Exec 'taskkill /IM "${APP_EXE}" /T /F'
  Sleep 500
  Delete "$SMPROGRAMS\${APP_NAME}.lnk"
  Delete "$DESKTOP\${APP_NAME}.lnk"
  StrCpy $0 "$INSTDIR" "" -11
  StrCmp $0 "${APP_NAME}" 0 +2
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\GhostCoder"
  ; User settings in %APPDATA%\GhostCoder are intentionally kept.
SectionEnd
