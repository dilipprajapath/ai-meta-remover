@echo off
REM ===========================================================================
REM  build_installer.bat — builds the Inno Setup installer (Setup .exe).
REM
REM  Requires:  Inno Setup 6 (https://jrsoftware.org/isdl.php) installed so
REM             that ISCC.exe is reachable, OR available via chocolatey:
REM                 choco install innosetup
REM
REM  Output:    dist\installer\Metavoid-Setup.exe
REM ===========================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0.."
set "ROOT=%CD%"

REM ---- 1. make sure the standalone exe exists -------------------------------
if not exist "%ROOT%\dist\Metavoid.exe" (
  echo The single-file exe was not found - running build.bat first...
  set "NO_PAUSE=1"
  call "%~dp0build.bat" || exit /b 1
)

REM ---- 2. find Inno Setup compiler -------------------------------------------
set "ISCC="
where iscc >nul 2>nul && set "ISCC=iscc"
if not defined ISCC (
  if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
)
if not defined ISCC (
  if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
)
if not defined ISCC (
  if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
)
if not defined ISCC (
  echo [ERROR] Inno Setup 6 not found. Install it from https://jrsoftware.org/isdl.php
  echo         or run:  winget install JRSoftware.InnoSetup
  pause
  exit /b 1
)

REM ---- 3. compile ------------------------------------------------------------
echo Compiling installer...
"%ISCC%" "%ROOT%\scripts\Metavoid.iss" || goto :err

REM Copy the newest versioned installer to the unversioned "latest" name.
REM Resolved by pattern, not hardcoded: the version lives in Metavoid.iss and
REM this script must not need editing every time it is bumped.
set "LATEST="
for /f "delims=" %%F in ('dir /b /o-d "%ROOT%\dist\installer\Metavoid-Setup-*.exe" 2^>nul') do (
  if not defined LATEST set "LATEST=%%F"
)
if not defined LATEST (
  echo [ERROR] ISCC reported success but no Metavoid-Setup-*.exe was produced.
  goto :err
)
copy /y "%ROOT%\dist\installer\%LATEST%" "%ROOT%\dist\installer\Metavoid-Setup.exe" >nul

echo.
echo ============================================================
echo  DONE. Installer created:
echo     %ROOT%\dist\installer\%LATEST%
echo     %ROOT%\dist\installer\Metavoid-Setup.exe
echo  Share this file - users double-click to install.
echo ============================================================
exit /b 0

:err
echo [ERROR] Installer build failed.
pause
exit /b 1
