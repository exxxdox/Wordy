# Wordy 开发启动脚本（显示控制台日志）
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

Write-Host '[dev] Starting Wordy...'
uv run python -m wordy
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Host "[dev] wordy exited with code $exitCode"
}
exit $exitCode
