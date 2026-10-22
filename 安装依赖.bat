@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "PY_EXE="

echo ============================================================
echo   AoE4 Replay Studio - environment check
echo ============================================================
echo.

rem ---- 1) find Python ----
for %%P in (python.exe) do (
  if exist "%%~$PATH:P" set "PY_EXE=%%~$PATH:P"
)
if not defined PY_EXE (
  for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%D\python.exe" set "PY_EXE=%%D\python.exe"
  )
)
if not defined PY_EXE (
  for /d %%D in ("C:\Python3*") do (
    if exist "%%D\python.exe" set "PY_EXE=%%D\python.exe"
  )
)
if not defined PY_EXE goto NOPY

echo [1/3] Python found: %PY_EXE%
"%PY_EXE%" -c "import sys;print('      version:',sys.version.split()[0]);sys.exit(0 if sys.version_info>=(3,10) else 1)"
if errorlevel 1 goto OLDPY

rem ---- 2) pip present? ----
echo [2/3] Checking pip ...
"%PY_EXE%" -m pip --version >nul 2>&1
if errorlevel 1 (
  echo       pip missing, bootstrapping ...
  "%PY_EXE%" -m ensurepip --upgrade
)

rem ---- 3) install optional deps ----
echo [3/3] Installing optional packages ...
echo.
"%PY_EXE%" run.py --install-deps
echo.
echo ------------------------------------------------------------
echo   Done. You can now run:  启动.bat
echo ------------------------------------------------------------
echo.
pause
goto EOF

:NOPY
echo.
echo [x] Python not found.
echo.
echo     This tool needs Python 3.10 or newer.
echo.
echo     Download (Windows installer, tick "Add python.exe to PATH"):
echo         https://www.python.org/downloads/
echo.
echo     Or install from Microsoft Store: search "Python 3.12".
echo.
pause
exit /b 1

:OLDPY
echo.
echo [x] Python is too old. Need 3.10 or newer.
echo     Download: https://www.python.org/downloads/
echo.
pause
exit /b 1

:EOF
endlocal
