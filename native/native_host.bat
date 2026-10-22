@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "LOG=%~dp0..\data\native_host.log"

rem 1) py launcher first: it never resolves to the Microsoft Store alias stub,
rem    which would just open the Store and immediately exit.
where py >nul 2>&1
if not errorlevel 1 (
  py -3 "native_host.py"
  if not errorlevel 1 goto :EOF
  goto :LOGFAIL
)

rem 2) python.exe from PATH, but reject the WindowsApps alias stub
set "PY_EXE="
for %%P in (python.exe) do (
  if exist "%%~$PATH:P" set "PY_EXE=%%~$PATH:P"
)
if defined PY_EXE (
  echo %PY_EXE% | find /i "WindowsApps" >nul && set "PY_EXE="
)

rem 3) versioned installs, newest first (lexicographic order happens to work)
if not defined PY_EXE (
  for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%D\python.exe" set "PY_EXE=%%D\python.exe"
  )
)
if not defined PY_EXE exit /b 1

"%PY_EXE%" "native_host.py"
if not errorlevel 1 goto :EOF

:LOGFAIL
rem Native messaging has no console: leave a breadcrumb instead of dying silently.
if not exist "%~dp0..\data" mkdir "%~dp0..\data"
echo [%DATE% %TIME%] native host failed to start (no usable Python 3.10+) >> "%LOG%"
exit /b 1

:EOF
endlocal
