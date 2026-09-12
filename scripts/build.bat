@echo off
REM ===========================================================================
REM  build.bat — one-click Windows build of the single-file .exe
REM
REM  Usage:      double-click this file (or run it from a cmd window)
REM  Requires:   Python 3.9 - 3.14 installed and on PATH (it will be found
REM              automatically). The right dependency set is chosen for your
REM              Python version. Everything else is downloaded into a
REM              throwaway virtual environment — nothing pollutes your
REM              system Python.
REM
REM  Output:     dist\Metavoid.exe   (single, double-clickable)
REM ===========================================================================
setlocal
cd /d "%~dp0.."
set "ROOT=%CD%"

REM ---- find a suitable Python -------------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  python --version >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.9-3.14 from python.org
    echo         and tick "Add python.exe to PATH", then run this again.
    pause
    exit /b 1
  )
  set "PY=python"
)
%PY% --version

REM ---- detect the 2-part Python version, e.g. 3.14 -> 44 (3*10+14) --------
set "PYDOT="
for /f "delims=" %%i in ('%PY% -c "import sys;print(sys.version.split()[0])"') do set "PYDOT=%%i"
if not defined PYDOT goto :err
set "PYVER=0"
set "PYA=%PYDOT:~0,1%"
set "PYB=%PYDOT:~2,2%"
set /a PYVER=%PYA%*10+%PYB% 2>nul || set "PYVER=0"
echo Detected Python %PYDOT%

REM ---- throwaway venv (kept outside the project, under %TEMP%) --------------
set "VENV=%TEMP%\metavoid_build_venv"
if not exist "%VENV%" (
  echo Creating build virtual environment...
  %PY% -m venv "%VENV%" || goto :err
)
call "%VENV%\Scripts\activate.bat"
python -m pip install --upgrade pip >nul

REM ---- install dependencies ------------------------------------------------
if %PYVER% GEQ 44 (
  REM Python 3.14+ : PyInstaller 6.10.0 and Pillow 10.4.0 do not support it.
  echo Python 3.14 detected - installing Python-3.14-compatible build deps...
  pip install "Pillow>=12.0,<13" piexif==1.1.3 defusedxml==0.7.1 ^
             Flask==3.0.3 "pyinstaller>=6.15,<7" "pyinstaller-hooks-contrib>=2024.8" || goto :err
) else (
  echo Installing pinned dependencies (this downloads Pillow, PyInstaller...)
  pip install -r requirements-build.txt || goto :err
)

REM ---- optional: native desktop window dependency (WebView2) -----------------
set "NATIVE_OK=1"
pip install "pywebview>=5.3,<7" >nul 2>nul
if errorlevel 1 (
  set "NATIVE_OK=0"
  echo.
  echo [i] pywebview ^(native desktop window^) could not be installed on this
  echo     Python version. The app will still build and open in your browser.
  echo.
)

REM ---- run PyInstaller --------------------------------------------------------
echo Building single-file executable (this can take a minute or two)...
python -m PyInstaller --noconfirm --clean "Metavoid.spec" || goto :err

echo.
echo ============================================================
echo  DONE. Your app is ready:
echo     %ROOT%\dist\Metavoid.exe
if "%NATIVE_OK%"=="0" (
  echo  Note: native window not bundled on this Python - the exe will
  echo        open the UI in your browser. Install Python 3.12 and rebuild
  echo        to get the desktop-window version.
)
echo ============================================================
if not defined NO_PAUSE pause
exit /b 0

:err
echo.
echo [ERROR] Build failed. Scroll up to see the message.
if not defined NO_PAUSE pause
exit /b 1
