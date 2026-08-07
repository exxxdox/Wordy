# Wordy 环境初始化脚本（uv 版）
# 用法：.\init.ps1 [-NoPause] [-PythonVersion 3.12]

param(
    [switch]$NoPause,
    [string]$PythonVersion = "3.12"
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Invoke-Pause {
    if (-not $NoPause) {
        Read-Host 'Press Enter to continue'
    }
}

# 1. 检查 uv 是否已安装
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host '[ERROR] uv not found. Install it first:'
    Write-Host '  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"'
    Invoke-Pause
    exit 1
}

Write-Host "[INFO] uv $(& uv --version)"

# 2. 确保 Python 版本固定
$pyVerFile = Join-Path $PSScriptRoot '.python-version'
if (-not (Test-Path -LiteralPath $pyVerFile)) {
    & uv python pin $PythonVersion
}

# 3. 同步依赖（自动创建 .venv、安装 Python、安装依赖）
Write-Host '[INFO] Syncing dependencies...'
& uv sync

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] uv sync failed with code $LASTEXITCODE"
    Invoke-Pause
    exit $LASTEXITCODE
}

Write-Host '[INFO] Environment ready.'
Write-Host "[INFO] Activate: .\.venv\Scripts\Activate.ps1"
Write-Host "[INFO] Run:     uv run python -m wordy"

Invoke-Pause
exit 0
