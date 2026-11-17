@echo off
rem Run from the project root so relative paths match wordy.ps1.
pushd "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0wordy.ps1" %*
set "code=%errorlevel%"
popd
exit /b %code%
