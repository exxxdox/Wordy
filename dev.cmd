@echo off
rem Resolve the script beside this launcher and forward optional action flags.
powershell.exe -NoProfile -File "%~dp0wordy.ps1" %*
exit /b %errorlevel%
