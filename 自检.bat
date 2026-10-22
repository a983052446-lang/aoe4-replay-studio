@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "PY_EXE="

for %%P in (python.exe) do (
  if exist "%%~$PATH:P" set "PY_EXE=%%~$PATH:P"
)
if not defined PY_EXE (
  for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%D\python.exe" set "PY_EXE=%%D\python.exe"
  )
)
if not defined PY_EXE goto NOPY

"%PY_EXE%" "run.py" --selftest
echo.
pause
goto EOF

:NOPY
echo [x] No usable Python found. Install Python 3.10+ and add it to PATH.
pause
exit /b 1

:EOF
endlocal
