@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "PY_EXE="

if not defined PY_EXE (
  for %%P in (python.exe) do (
    if exist "%%~$PATH:P" set "PY_EXE=%%~$PATH:P"
  )
)
if not defined PY_EXE goto NOPY

echo [1/2] Packing extension ...
"%PY_EXE%" "native\pack_extension.py" --ext "extension" --out "data\lcs-recorder.crx" --key "data\lcs-recorder.pem" --print-id > "data\extid.txt" 2> "data\pack.err.txt"
if errorlevel 1 goto PACKFAIL

set "EXTID="
for /f "usebackq delims=" %%i in ("data\extid.txt") do set EXTID=%%i
if "%EXTID%"=="" goto PACKFAIL
echo       Extension ID = %EXTID%

echo [2/2] Registering native host and external extension ...
"%PY_EXE%" "native\install_host.py" --ext-id "%EXTID%"
if errorlevel 1 goto REGFAIL

echo.
echo Done.
echo   1) Quit Chrome completely (including tray), then open it again.
echo   2) Open chrome://extensions and look for "LCS recorder".
echo   3) Click the toolbar icon to start/stop recording.
echo.
echo Uninstall: "%PY_EXE%" "native\install_host.py" --ext-id %EXTID% --uninstall
echo.
pause
goto EOF

:NOPY
echo [x] No usable Python found. Install Python 3.10+ and add it to PATH.
pause
exit /b 1

:PACKFAIL
echo [x] Failed to pack the extension. See data\pack.err.txt
echo --- error detail ---
type "data\pack.err.txt"
pause
exit /b 1

:REGFAIL
echo [x] Failed to register the native host.
pause
exit /b 1

:EOF
endlocal
