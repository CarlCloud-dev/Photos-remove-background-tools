@echo off
REM =============================================
REM 将 PyInstaller 打包的 backend 产物拷贝到 Electron resources 目录
REM 供 electron-builder extraResources 打包
REM =============================================
setlocal
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%\..\.."
set "DIST_DIR=%SCRIPT_DIR%\..\dist\backend"
set "TARGET_DIR=%PROJECT_ROOT%\electron\resources\backend"

if not exist "%DIST_DIR%\backend.exe" (
    echo [错误] 未找到 %DIST_DIR%\backend.exe
    echo 请先执行：pyinstaller --clean build_backend.spec
    exit /b 1
)

if exist "%TARGET_DIR%" rmdir /S /Q "%TARGET_DIR%"
mkdir "%TARGET_DIR%"

xcopy /E /I /Y /Q "%DIST_DIR%\*" "%TARGET_DIR%\"
echo [完成] 已拷贝 backend 产物到 %TARGET_DIR%
endlocal
