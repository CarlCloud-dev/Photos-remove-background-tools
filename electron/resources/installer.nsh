; Custom NSIS behavior for the installed edition.
; Users choose a parent directory; the application folder is always appended here.

!include "LogicLib.nsh"
!include "nsDialogs.nsh"

!define PHOTOS_INSTALL_DIR_NAME "Photos-RMBG-tools"

!ifndef BUILD_UNINSTALLER
Var PhotosInstallDirField

!macro customPageAfterChangeDir
  Page custom PhotosInstallDirectoryPageCreate PhotosInstallDirectoryPageLeave
!macroend

; Keep exactly one Photos-RMBG-tools suffix, both when a path is selected
; through Browse and when it is entered manually.
Function PhotosEnsureApplicationDirectory
  Push $0
  Push $1
  Push $2

  StrCmp $INSTDIR "" photos_install_dir_empty

  ; Normalize one trailing separator before testing the folder suffix.
  StrCpy $0 $INSTDIR 1 -1
  StrCmp $0 "\" 0 +2
    StrCpy $INSTDIR $INSTDIR -1

  StrLen $0 "${PHOTOS_INSTALL_DIR_NAME}"
  StrCpy $1 $INSTDIR $0 -$0
  StrCmp $1 "${PHOTOS_INSTALL_DIR_NAME}" photos_install_dir_done

  ; Replace the default Electron Builder product folder when it is present.
  StrLen $0 "${APP_FILENAME}"
  StrCpy $1 $INSTDIR $0 -$0
  StrCmp $1 "${APP_FILENAME}" 0 photos_append_install_dir
  StrCpy $2 $INSTDIR -$0
  StrCpy $INSTDIR "$2\${PHOTOS_INSTALL_DIR_NAME}"
  Goto photos_install_dir_done

photos_append_install_dir:
  StrCpy $INSTDIR "$INSTDIR\${PHOTOS_INSTALL_DIR_NAME}"
  Goto photos_install_dir_done

photos_install_dir_empty:
  StrCpy $INSTDIR "$LOCALAPPDATA\Programs\${PHOTOS_INSTALL_DIR_NAME}"

photos_install_dir_done:
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Function PhotosInstallDirectoryPageCreate
  Call PhotosEnsureApplicationDirectory

  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 28u "请选择安装父目录。安装器会自动创建 Photos-RMBG-tools 文件夹。$\r$\n模型、配置、插件、日志和输出都会保存在该安装文件夹内。"
  Pop $0
  ${NSD_CreateText} 0 32u 76% 12u "$INSTDIR"
  Pop $PhotosInstallDirField
  ${NSD_CreateButton} 78% 32u 22% 12u "浏览..."
  Pop $0
  ${NSD_OnClick} $0 PhotosBrowseInstallDirectory

  nsDialogs::Show
FunctionEnd

Function PhotosBrowseInstallDirectory
  Push $0
  nsDialogs::SelectFolderDialog "选择安装父目录" "$INSTDIR"
  Pop $0
  StrCmp $0 "error" photos_browse_done
  StrCmp $0 "" photos_browse_done

  StrCpy $INSTDIR $0
  Call PhotosEnsureApplicationDirectory
  ${NSD_SetText} $PhotosInstallDirField "$INSTDIR"

photos_browse_done:
  Pop $0
FunctionEnd

Function PhotosInstallDirectoryPageLeave
  ${NSD_GetText} $PhotosInstallDirField $0
  StrCmp $0 "" 0 photos_install_dir_entered
  MessageBox MB_ICONEXCLAMATION|MB_OK "请选择安装父目录。"
  Abort

photos_install_dir_entered:
  StrCpy $INSTDIR $0
  Call PhotosEnsureApplicationDirectory
FunctionEnd
!endif

; Models are downloaded by the application after the user chooses a mirror.
; backend/config.py always stores them in $INSTDIR\models.
!macro customInstall
  IfFileExists "$INSTDIR\${APP_EXECUTABLE_FILENAME}" photos_app_files_ready
  MessageBox MB_ICONSTOP|MB_OK "应用文件解压失败，安装已取消。请关闭本安装器后重新运行最新安装包。"
  Abort

photos_app_files_ready:
  CreateDirectory "$INSTDIR\models"
  CreateDirectory "$INSTDIR\logs"
  CreateDirectory "$INSTDIR\output"
  CreateDirectory "$INSTDIR\plugins"
  CreateDirectory "$INSTDIR\profile"
  CreateDirectory "$INSTDIR\temp"

  ; electron-builder creates the link from the executable resource.  Explicitly
  ; set the shipped ICO here as well so upgrading an older release cannot keep
  ; Windows Explorer's generic-icon cache on the desktop shortcut.
  IfFileExists "$INSTDIR\resources\icon.ico" 0 +5
  Delete "$DESKTOP\${SHORTCUT_NAME}.lnk"
  CreateShortCut "$DESKTOP\${SHORTCUT_NAME}.lnk" "$INSTDIR\${APP_EXECUTABLE_FILENAME}" "" "$INSTDIR\resources\icon.ico" 0 "" "" "${APP_DESCRIPTION}"
  ClearErrors
  WinShell::SetLnkAUMI "$DESKTOP\${SHORTCUT_NAME}.lnk" "${APP_ID}"
!macroend
