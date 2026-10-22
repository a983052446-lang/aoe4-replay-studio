@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "PY_EXE="

rem 依次试：py 启动器 -> PATH 里的 python -> 常见安装位置
for %%C in (py.exe) do (
  if exist "%%~$PATH:C" set "PY_EXE=%%~$PATH:C"
)
if defined PY_EXE (
  "%PY_EXE%" -3 "run.py" %*
  goto :CHECK
)

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

echo [1/2] Interpreter: %PY_EXE%
echo [2/2] Starting local service ...
"%PY_EXE%" "run.py" %*

:CHECK
if errorlevel 1 goto FAILED
goto EOF

:NOPY
echo.
echo [x] No usable Python found.
echo     Install Python 3.10+ and tick "Add python.exe to PATH".
echo     https://www.python.org/downloads/
echo.
pause
exit /b 1

:FAILED
echo.
echo [x] Failed to start. See the error above.
pause
exit /b 1

:EOF
endlocal
