@echo off
REM Runs ON the Windows machine (in the deploy dir) after push.sh extracts new code.
REM Stops the old instance, ensures the venv + deps, (re)registers the startup task, relaunches.
REM The app runs under pythonw.exe (no console) and logs to app.log via file logging in the app.
REM Arg 1 (optional): python.exe used to create the venv; defaults to `python` on PATH.
setlocal
cd /d "%~dp0"
set "PY=%~1"
if "%PY%"=="" set "PY=python"

echo [1/5] Stopping any running instance...
powershell -NoProfile -Command "Get-Process python,pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path -like '*sensor_reader*' } | Stop-Process -Force" 2>nul

echo [2/5] Ensuring virtual environment...
if not exist ".venv\Scripts\pythonw.exe" "%PY%" -m venv .venv

echo [3/5] Installing/refreshing dependencies...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements-runtime.txt

echo [4/5] Registering startup task (runs at logon, restarts on crash)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$exe=Join-Path $PWD.Path '.venv\Scripts\pythonw.exe'; $a=New-ScheduledTaskAction -Execute $exe -Argument '-m lywsd03mmc_monitor' -WorkingDirectory $PWD.Path; $t=New-ScheduledTaskTrigger -AtLogOn; $s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero); Register-ScheduledTask -TaskName 'sensor_reader' -Action $a -Trigger $t -Settings $s -Force | Out-Null"

echo [5/5] Opening firewall for LAN access (best effort; needs admin)...
netsh advfirewall firewall show rule name="sensor_reader 8787" >nul 2>&1 || netsh advfirewall firewall add rule name="sensor_reader 8787" dir=in action=allow protocol=TCP localport=8787 >nul 2>&1

echo Launching app...
powershell -NoProfile -Command "Start-ScheduledTask -TaskName sensor_reader"
echo remote-update done.
endlocal
