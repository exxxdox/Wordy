# Easy TTS 启动脚本
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

Write-Host '[INFO] Starting Easy TTS...'

# uv run 自动使用 .venv 中的 Python，无需手动拼接路径
uv run python main.py
$exitCode = $LASTEXITCODE

Write-Host ''
if ($exitCode -ne 0) {
    Write-Host "[ERROR] main.py exited with code $exitCode"
}

Read-Host 'Press Enter to continue'
exit $exitCode
