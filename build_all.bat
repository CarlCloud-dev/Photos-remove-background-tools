@echo off
REM ============================================================
REM  Smart Cutout Tool - repeatable Windows release build
REM  Keep this file ASCII-only for cmd.exe compatibility.
REM ============================================================
setlocal EnableExtensions DisableDelayedExpansion

cd /d "%~dp0"
set "PROJECT_ROOT=%CD%"
set "BUILD_LOG=%PROJECT_ROOT%\build_all.log"
set "BACKEND_DIR=%PROJECT_ROOT%\backend"
set "VENV_PY=%BACKEND_DIR%\venv\Scripts\python.exe"
set "BACKEND_DIST=%BACKEND_DIR%\dist\backend"
set "PACKAGED_BACKEND=%PROJECT_ROOT%\electron\resources\backend"
set "RELEASE_DIR=%PROJECT_ROOT%\release"
set "PY_DEPS_STAMP=%BACKEND_DIR%\venv\.rmbg-requirements.txt"
set "PY_DEPS_CHANGED=0"
set "FULL_REBUILD=0"
if /I "%~1"=="--full" set "FULL_REBUILD=1"
REM Prefer China mirrors for Electron and electron-builder binary downloads.
set "ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/"
set "ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/"

echo ============================================================ > "%BUILD_LOG%"
echo [BUILD START] %DATE% %TIME% >> "%BUILD_LOG%"
echo PROJECT_ROOT=%PROJECT_ROOT% >> "%BUILD_LOG%"
echo ELECTRON_MIRROR=%ELECTRON_MIRROR% >> "%BUILD_LOG%"
echo ELECTRON_BUILDER_BINARIES_MIRROR=%ELECTRON_BUILDER_BINARIES_MIRROR% >> "%BUILD_LOG%"
echo ============================================================ >> "%BUILD_LOG%"

echo ============================================================
echo   Smart Cutout Tool - Release Build
echo ============================================================
echo.
echo This build creates a Windows installer.
echo Close any running installed application before continuing.
if "%FULL_REBUILD%"=="1" echo Build mode: full rebuild.
if not "%FULL_REBUILD%"=="1" echo Build mode: incremental. Use build_all.bat --full after dependency changes.
echo.

echo [1/6] Check Python, Node.js and npm...
where python >nul 2>&1
if not errorlevel 1 goto python_ok
echo [ERROR] Python was not found in PATH.
echo [ERROR] Python was not found in PATH. >> "%BUILD_LOG%"
goto :error

:python_ok
python --version >> "%BUILD_LOG%" 2>&1
where node >nul 2>&1
if not errorlevel 1 goto node_ok
echo [ERROR] Node.js was not found in PATH.
echo [ERROR] Node.js was not found in PATH. >> "%BUILD_LOG%"
goto :error

:node_ok
node --version >> "%BUILD_LOG%" 2>&1
where npm >nul 2>&1
if not errorlevel 1 goto npm_ok
echo [ERROR] npm was not found in PATH.
echo [ERROR] npm was not found in PATH. >> "%BUILD_LOG%"
goto :error

:npm_ok
REM npm.cmd must be invoked through CALL, or cmd.exe will not return here.
call npm --version >> "%BUILD_LOG%" 2>&1

echo [2/6] Prepare Python virtual environment...
if exist "%VENV_PY%" goto venv_ready
echo Creating backend virtual environment...
echo [CMD] python -m venv backend\venv >> "%BUILD_LOG%"
python -m venv "%BACKEND_DIR%\venv" >> "%BUILD_LOG%" 2>&1
if not errorlevel 1 goto venv_ready
echo [ERROR] Failed to create backend virtual environment.
echo [ERROR] Failed to create backend virtual environment. >> "%BUILD_LOG%"
goto :error

:venv_ready
"%VENV_PY%" --version >> "%BUILD_LOG%" 2>&1
if not errorlevel 1 goto venv_usable
echo [ERROR] backend virtual environment is invalid.
echo         Delete backend\venv manually, then rerun this script.
echo [ERROR] backend virtual environment is invalid. >> "%BUILD_LOG%"
goto :error

:venv_usable
echo [3/6] Check Python dependencies...
if "%FULL_REBUILD%"=="1" goto install_python_dependencies
if not exist "%PY_DEPS_STAMP%" goto install_python_dependencies
fc /b "%BACKEND_DIR%\requirements.txt" "%PY_DEPS_STAMP%" >nul 2>&1
if not errorlevel 1 goto python_dependencies_ready

:install_python_dependencies
set "PY_DEPS_CHANGED=1"
if not "%FULL_REBUILD%"=="1" goto install_python_requirements
echo [CMD] "%VENV_PY%" -m pip install --upgrade pip >> "%BUILD_LOG%"
"%VENV_PY%" -m pip install --upgrade pip >> "%BUILD_LOG%" 2>&1
if errorlevel 1 goto python_dependencies_failed

:install_python_requirements
echo [CMD] "%VENV_PY%" -m pip install -r backend\requirements.txt >> "%BUILD_LOG%"
"%VENV_PY%" -m pip install -r "%BACKEND_DIR%\requirements.txt" >> "%BUILD_LOG%" 2>&1
if errorlevel 1 goto python_dependencies_failed
copy /y "%BACKEND_DIR%\requirements.txt" "%PY_DEPS_STAMP%" >> "%BUILD_LOG%" 2>&1
if not errorlevel 1 goto python_dependencies_ready
goto python_dependencies_failed

:python_dependencies_failed
echo [ERROR] Python dependency installation failed. See build_all.log.
echo [ERROR] Python dependency installation failed. >> "%BUILD_LOG%"
goto :error

:python_dependencies_ready
echo [4/6] Package Python backend...
if "%FULL_REBUILD%"=="1" goto remove_backend_dist
if "%PY_DEPS_CHANGED%"=="1" goto remove_backend_dist
goto build_backend

:remove_backend_dist
if not exist "%BACKEND_DIST%" goto remove_backend_build
rmdir /s /q "%BACKEND_DIST%" >> "%BUILD_LOG%" 2>&1
if not exist "%BACKEND_DIST%" goto remove_backend_build
echo [ERROR] Previous backend build is locked. Close the packaged app and retry.
echo [ERROR] Previous backend build is locked. >> "%BUILD_LOG%"
goto :error

:remove_backend_build
if not exist "%BACKEND_DIR%\build\build_backend" goto build_backend
rmdir /s /q "%BACKEND_DIR%\build\build_backend" >> "%BUILD_LOG%" 2>&1
if not exist "%BACKEND_DIR%\build\build_backend" goto build_backend
echo [ERROR] Previous PyInstaller work directory is locked.
echo [ERROR] Previous PyInstaller work directory is locked. >> "%BUILD_LOG%"
goto :error

:build_backend
set "PYINSTALLER_ARGS=-y"
if "%FULL_REBUILD%"=="1" set "PYINSTALLER_ARGS=--clean -y"
if "%PY_DEPS_CHANGED%"=="1" set "PYINSTALLER_ARGS=--clean -y"
pushd "%BACKEND_DIR%"
echo [CMD] "%VENV_PY%" -m PyInstaller %PYINSTALLER_ARGS% build_backend.spec >> "%BUILD_LOG%"
"%VENV_PY%" -m PyInstaller %PYINSTALLER_ARGS% build_backend.spec >> "%BUILD_LOG%" 2>&1
set "PYINSTALLER_EXIT=%ERRORLEVEL%"
popd
if "%PYINSTALLER_EXIT%"=="0" goto backend_packaged
echo [ERROR] PyInstaller failed. See build_all.log.
echo [ERROR] PyInstaller failed. >> "%BUILD_LOG%"
goto :error

:backend_packaged
if exist "%BACKEND_DIST%\backend.exe" goto copy_backend
echo [ERROR] backend.exe was not generated.
echo [ERROR] backend.exe was not generated. >> "%BUILD_LOG%"
goto :error

:copy_backend
if not exist "%PACKAGED_BACKEND%" goto create_packaged_backend
rmdir /s /q "%PACKAGED_BACKEND%" >> "%BUILD_LOG%" 2>&1
if not exist "%PACKAGED_BACKEND%" goto create_packaged_backend
echo [ERROR] Packaged backend directory is locked. Close the packaged app and retry.
echo [ERROR] Packaged backend directory is locked. >> "%BUILD_LOG%"
goto :error

:create_packaged_backend
mkdir "%PACKAGED_BACKEND%" >> "%BUILD_LOG%" 2>&1
if exist "%PACKAGED_BACKEND%" goto copy_backend_files
echo [ERROR] Cannot create electron resources backend directory.
echo [ERROR] Cannot create electron resources backend directory. >> "%BUILD_LOG%"
goto :error

:copy_backend_files
echo [CMD] xcopy /E /I /Y /Q backend\dist\backend electron\resources\backend >> "%BUILD_LOG%"
xcopy /E /I /Y /Q "%BACKEND_DIST%\*" "%PACKAGED_BACKEND%\" >> "%BUILD_LOG%" 2>&1
if not errorlevel 1 goto backend_copied
echo [ERROR] Copying packaged backend failed.
echo [ERROR] Copying packaged backend failed. >> "%BUILD_LOG%"
goto :error

:backend_copied
if exist "%PACKAGED_BACKEND%\backend.exe" goto node_dependencies
echo [ERROR] Packaged backend executable is missing after copy.
echo [ERROR] Packaged backend executable is missing after copy. >> "%BUILD_LOG%"
goto :error

:node_dependencies
echo [5/6] Check Node.js dependencies...
if "%FULL_REBUILD%"=="1" goto install_node_dependencies
if exist "%PROJECT_ROOT%\node_modules\electron-builder\package.json" goto node_dependencies_ready

:install_node_dependencies
echo [CMD] call npm ci >> "%BUILD_LOG%"
call npm ci >> "%BUILD_LOG%" 2>&1
if not errorlevel 1 goto node_dependencies_ready
echo [ERROR] npm ci failed. See build_all.log.
echo [ERROR] npm ci failed. >> "%BUILD_LOG%"
goto :error

:node_dependencies_ready
echo Node.js dependencies ready. >> "%BUILD_LOG%"
goto clean_release

:clean_release
echo [6/6] Build renderer and package release artifacts...
if not exist "%RELEASE_DIR%" goto run_electron_build
rmdir /s /q "%RELEASE_DIR%" >> "%BUILD_LOG%" 2>&1
if not exist "%RELEASE_DIR%" goto run_electron_build
echo [ERROR] release directory is locked. Close the packaged app and retry.
echo [ERROR] release directory is locked. >> "%BUILD_LOG%"
goto :error

:run_electron_build
echo [CMD] call npm run build >> "%BUILD_LOG%"
call npm run build >> "%BUILD_LOG%" 2>&1
if not errorlevel 1 goto verify_release
echo [ERROR] Electron packaging failed. See build_all.log.
echo [ERROR] Electron packaging failed. >> "%BUILD_LOG%"
goto :error

:verify_release
set "INSTALLER_FILE="
for %%F in ("%RELEASE_DIR%\*Setup-x64.exe") do if exist "%%~fF" set "INSTALLER_FILE=%%~fF"
if defined INSTALLER_FILE goto build_complete
echo [ERROR] Windows installer is missing from release.
echo [ERROR] Windows installer is missing from release. >> "%BUILD_LOG%"
goto :error

:build_complete
REM NSIS installer is already verified above. win-unpacked is only the
REM electron-builder staging directory, so do not keep it as a release artifact.
if exist "%RELEASE_DIR%\win-unpacked" rmdir /s /q "%RELEASE_DIR%\win-unpacked" >> "%BUILD_LOG%" 2>&1
if exist "%RELEASE_DIR%\win-unpacked" echo [WARN] Could not remove win-unpacked. >> "%BUILD_LOG%"
echo.
echo ============================================================
echo   BUILD COMPLETE
echo   Installer: %INSTALLER_FILE%
echo ============================================================
echo Installer: %INSTALLER_FILE% >> "%BUILD_LOG%"
dir /b "%RELEASE_DIR%\*.exe" 2>nul
echo.
echo Full log: %BUILD_LOG%
echo.
pause
exit /b 0

:error
echo.
echo ============================================================
echo   BUILD FAILED
echo   Review: %BUILD_LOG%
echo ============================================================
echo.
pause
exit /b 1
