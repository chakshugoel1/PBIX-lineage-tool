@echo off
setlocal enabledelayedexpansion

rem ============================================================================
rem  PBIX Lineage Tool - one-file bootstrap installer
rem ============================================================================
rem  Double-click this file on a brand-new machine. It downloads
rem  bootstrap-install.ps1 from GitHub and runs it, which installs Python/Git
rem  if needed, downloads the tool to a private per-user folder, sets up a
rem  virtual environment, and creates a Start Menu shortcut. This file can be
rem  copied/emailed/shared on its own - everything else it needs, it fetches
rem  itself.
rem
rem  Requires: PowerShell (built into Windows) and internet access to GitHub.
rem  If your network blocks raw.githubusercontent.com, ask for
rem  bootstrap-install.ps1 directly instead and run it with:
rem    powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap-install.ps1
rem ============================================================================

set "SCRIPT_URL=https://raw.githubusercontent.com/chakshugoel1/PBIX-lineage-tool/main/bootstrap-install.ps1"
set "TEMP_SCRIPT=%TEMP%\pbix-lineage-bootstrap-install.ps1"

echo ===============================================================
echo   PBIX Lineage Tool - Installer
echo ===============================================================
echo.
echo This will:
echo   - install Python and Git if missing (via winget)
echo   - download the tool to %%LOCALAPPDATA%%\PBIXLineageTool
echo   - set up a private virtual environment and install dependencies
echo   - create a Start Menu shortcut
echo.
echo You can close this window at any time; nothing runs until you continue.
echo.
pause

echo.
echo Downloading installer script...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -UseBasicParsing -Uri '%SCRIPT_URL%' -OutFile '%TEMP_SCRIPT%'"
if errorlevel 1 goto DOWNLOAD_FAILED
if not exist "%TEMP_SCRIPT%" goto DOWNLOAD_FAILED

echo Download OK. Running installer...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%TEMP_SCRIPT%"
set "EXITCODE=%errorlevel%"

echo.
if "%EXITCODE%"=="0" (
  echo ===============================================================
  echo   Setup finished successfully.
  echo ===============================================================
) else (
  echo ===============================================================
  echo   Setup did not finish ^(exit code %EXITCODE%^).
  echo   Scroll up to see what failed. Fixing that and double-clicking
  echo   this file again will pick up from where it left off - steps
  echo   already completed are skipped automatically.
  echo ===============================================================
)
pause
exit /b %EXITCODE%

:DOWNLOAD_FAILED
echo.
echo ===============================================================
echo   Could not download the installer.
echo ===============================================================
echo Check your internet connection, or whether this network blocks
echo raw.githubusercontent.com (some corporate proxies do).
echo.
echo If it's blocked, ask whoever sent you this file for
echo bootstrap-install.ps1 directly, put it next to this file, and run:
echo   powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap-install.ps1
echo.
pause
exit /b 1
