@echo off
REM ============================================================
REM  run_build_safe.bat - OUTER LAUNCHER (SAFE, NO-FLASH GUARANTEED)
REM  ------------------------------------------------------------
REM  This file stays 100% ASCII. It NEVER contains Chinese.
REM  It simply uses "cmd /k" to launch build_all.bat inside a
REM  brand-new interactive cmd window. Even if the inner script
REM  crashes/aborts/exits on its very first line for whatever
REM  reason (bad encoding, registry AutoRun, missing file ...),
REM  the outer interactive cmd window (because of /k) will STAY
REM  OPEN and show the last output and the error level.
REM  This is the most reliable "no-flash" pattern on Windows.
REM  ------------------------------------------------------------
REM  Also we do the very minimum needed here (no helpers, no
REM  delayed expansion, no parentheses block, no for /f) so the
REM  launcher itself cannot cause the flash. If the launcher
REM  flashes too then the issue is OUTSIDE any script (for
REM  example the antivirus kills cmd.exe right away).
REM ============================================================
REM Jump to script own folder (handles drives + spaces via /d).
cd /d "%~dp0"

REM 1) Sanity: make sure the target actually exists in this dir.
if not exist "build_all.bat" (
    echo.
    echo [SAFE-LAUNCHER ERROR] build_all.bat NOT FOUND next to me.
    echo Current directory = %cd%
    echo Listing of *.bat files here:
    dir /b *.bat
    echo.
    echo Press any key to exit...
    pause >nul
    exit /b 2
)

REM 2) Launch inner build script inside a STICKY cmd window (/k).
REM    The weird double-quote pattern ("" ... "") is intentional
REM    and required by cmd.exe /k when the target path contains
REM    spaces. See: cmd /? section on /k.
echo.
echo ============================================================
echo   RemoveBG Build - SAFE LAUNCHER (window guaranteed to stay)
echo   Starting: build_all.bat  (via cmd /k - window never auto-closes)
echo ============================================================
echo.
REM If user clicks "X" on the window after build, that's fine.
cmd.exe /k ""%~dp0build_all.bat""

REM If we ever reach here (user manually typed exit inside inner cmd),
REM pause before launcher exits too so they can read any last message.
echo.
echo [SAFE-LAUNCHER] Inner build cmd has exited. Build log = %~dp0build_all.log
echo Press any key to close this launcher...
pause >nul
exit /b 0
