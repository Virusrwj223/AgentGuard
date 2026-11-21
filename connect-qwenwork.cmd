@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run start.cmd once to create the Python environment. Then open this setup again.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r integrations\requirements-mcp.txt
if errorlevel 1 goto failed
set "worker_skill=%USERPROFILE%\.qwenwork\skills\northline-web-worker"
if not exist "%worker_skill%" mkdir "%worker_skill%"
if errorlevel 1 goto failed
copy /y "agents\skills\northline-web-worker\SKILL.md" "%worker_skill%\SKILL.md" >nul
if errorlevel 1 goto failed
powershell -NoProfile -Command "$json = & '.\.venv\Scripts\python.exe' 'run.py' '--qwenwork-config'; if ($LASTEXITCODE -ne 0) { exit 1 }; ($json -join [Environment]::NewLine) | Set-Clipboard"
if errorlevel 1 goto failed
echo Worker installed. Private connector settings have been copied to your clipboard.
echo In QwenWork open Extensions, Connectors, + Add, Paste JSON configuration.
echo Paste, import, and enable northline-web. Then start a QwenWork conversation:
echo Use northline-web-worker to serve the travel desk and keep listening for website tasks.
pause
exit /b 0
:failed
echo Setup failed. Review the message above.
pause
exit /b 1
