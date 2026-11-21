@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>&1
  if errorlevel 1 (
    python -m venv .venv
  ) else (
    py -3 -m venv .venv
  )
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" run.py --open-browser
if errorlevel 1 goto failed
exit /b 0
:failed
echo Setup or startup failed. Review the message above.
pause
exit /b 1
