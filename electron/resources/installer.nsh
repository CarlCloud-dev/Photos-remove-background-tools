; Custom NSIS behavior for the installed edition.
; Users choose a parent directory; the application folder is always appended here.

!include "LogicLib.nsh"
!include "nsDialogs.nsh"

!define PHOTOS_INSTALL_DIR_NAME "Photos-RMBG-tools"

!ifndef BUILD_UNINSTALLER
Var PhotosInstallDirField
Var PhotosInstallBrowseButton
Var PhotosUpgradeMode

!macro customPageAfterChangeDir
  Page custom PhotosInstallDirectoryPageCreate PhotosInstallDirectoryPageLeave
!macroend

; Silent updates do not display the custom location page. Preserve data before
; electron-builder invokes the previous uninstaller in that case as well.
!macro customInit
  ${If} ${Silent}
    Call PhotosPrepareUpgradeData
  ${EndIf}
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
  StrCpy $PhotosUpgradeMode "0"
  IfFileExists "$INSTDIR\${APP_EXECUTABLE_FILENAME}" 0 photos_install_page_new
  StrCpy $PhotosUpgradeMode "1"

photos_install_page_new:
  StrCpy $1 "请选择安装父目录。安装器会自动创建 Photos-RMBG-tools 文件夹。$\r$\n模型、配置、插件、日志和输出都会保存在该安装文件夹内。"
  StrCmp $PhotosUpgradeMode "1" 0 +2
    StrCpy $1 "检测到已安装版本，将在原位置升级。模型、CUDA 运行时、配置、插件、日志和输出会完整保留。"

  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 28u "$1"
  Pop $0
  ${NSD_CreateText} 0 32u 76% 12u "$INSTDIR"
  Pop $PhotosInstallDirField
  ${NSD_CreateButton} 78% 32u 22% 12u "浏览..."
  Pop $PhotosInstallBrowseButton
  ${NSD_OnClick} $PhotosInstallBrowseButton PhotosBrowseInstallDirectory
  StrCmp $PhotosUpgradeMode "1" 0 photos_install_page_show
  EnableWindow $PhotosInstallDirField 0
  EnableWindow $PhotosInstallBrowseButton 0

photos_install_page_show:
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
  Call PhotosPrepareUpgradeData
FunctionEnd

; Move persistent data outside $INSTDIR before electron-builder starts the old
; uninstaller. The move is on the same volume, so even multi-GB model/CUDA
; directories are moved atomically instead of copied.
Function PhotosPreserveUpgradeDirectory
  Exch $0
  IfFileExists "$INSTDIR\$0\*.*" 0 photos_preserve_upgrade_dir_done
  CreateDirectory "$INSTDIR.__upgrade-data"
  ClearErrors
  Rename "$INSTDIR\$0" "$INSTDIR.__upgrade-data\$0"
  ${If} ${Errors}
    Call PhotosRestoreUpgradeData
    MessageBox MB_ICONSTOP|MB_OK "无法保护升级数据目录：$0。请关闭正在使用该目录的程序后重试。"
    Abort
  ${EndIf}
photos_preserve_upgrade_dir_done:
  Pop $0
FunctionEnd

Function PhotosPreserveUpgradeConfig
  IfFileExists "$INSTDIR\config.json" 0 photos_preserve_upgrade_config_done
  CreateDirectory "$INSTDIR.__upgrade-data"
  ClearErrors
  Rename "$INSTDIR\config.json" "$INSTDIR.__upgrade-data\config.json"
  ${If} ${Errors}
    Call PhotosRestoreUpgradeData
    MessageBox MB_ICONSTOP|MB_OK "无法保护升级配置。请关闭正在使用该文件的程序后重试。"
    Abort
  ${EndIf}
photos_preserve_upgrade_config_done:
FunctionEnd

Function PhotosRestoreUpgradeDirectory
  Exch $0
  IfFileExists "$INSTDIR.__upgrade-data\$0\*.*" 0 photos_restore_upgrade_dir_done
  IfFileExists "$INSTDIR\$0\*.*" 0 photos_restore_upgrade_dir_move
  Goto photos_restore_upgrade_dir_done
photos_restore_upgrade_dir_move:
  RMDir "$INSTDIR\$0"
  Rename "$INSTDIR.__upgrade-data\$0" "$INSTDIR\$0"
photos_restore_upgrade_dir_done:
  Pop $0
FunctionEnd

Function PhotosRestoreUpgradeData
  Push "models"
  Call PhotosRestoreUpgradeDirectory
  Push "runtime"
  Call PhotosRestoreUpgradeDirectory
  Push "output"
  Call PhotosRestoreUpgradeDirectory
  Push "logs"
  Call PhotosRestoreUpgradeDirectory
  Push "plugins"
  Call PhotosRestoreUpgradeDirectory
  Push "profile"
  Call PhotosRestoreUpgradeDirectory
  Push "temp"
  Call PhotosRestoreUpgradeDirectory

  IfFileExists "$INSTDIR.__upgrade-data\config.json" 0 photos_restore_upgrade_cleanup
  Delete "$INSTDIR\config.json"
  Rename "$INSTDIR.__upgrade-data\config.json" "$INSTDIR\config.json"
photos_restore_upgrade_cleanup:
  RMDir "$INSTDIR.__upgrade-data"
FunctionEnd

Function PhotosPrepareUpgradeData
  IfFileExists "$INSTDIR\${APP_EXECUTABLE_FILENAME}" 0 photos_prepare_upgrade_done
  ; Recover a data backup left by an interrupted earlier upgrade before moving
  ; anything again. If a collision remains, stop rather than risking data loss.
  IfFileExists "$INSTDIR.__upgrade-data\*.*" 0 photos_prepare_upgrade_move
  Call PhotosRestoreUpgradeData
  IfFileExists "$INSTDIR.__upgrade-data\*.*" 0 photos_prepare_upgrade_move
  MessageBox MB_ICONSTOP|MB_OK "检测到未完成升级留下的数据保留目录：$INSTDIR.__upgrade-data。为避免覆盖模型或配置，安装已取消。"
  Abort

photos_prepare_upgrade_move:
  Push "models"
  Call PhotosPreserveUpgradeDirectory
  Push "runtime"
  Call PhotosPreserveUpgradeDirectory
  Push "output"
  Call PhotosPreserveUpgradeDirectory
  Push "logs"
  Call PhotosPreserveUpgradeDirectory
  Push "plugins"
  Call PhotosPreserveUpgradeDirectory
  Push "profile"
  Call PhotosPreserveUpgradeDirectory
  Push "temp"
  Call PhotosPreserveUpgradeDirectory
  Call PhotosPreserveUpgradeConfig
photos_prepare_upgrade_done:
FunctionEnd

Function .onUserAbort
  Call PhotosRestoreUpgradeData
FunctionEnd

Function .onInstFailed
  Call PhotosRestoreUpgradeData
FunctionEnd
!endif

; Models are downloaded by the application after the user chooses a mirror.
; backend/config.py always stores them in $INSTDIR\models.
!macro customInstall
  ; Restore the model/runtime/settings folders moved before the old-version
  ; uninstaller ran. This executes after fresh program files are in place.
  Call PhotosRestoreUpgradeData
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
