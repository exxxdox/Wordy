# Easy TTS 打包脚本（uv 版）
# 用法：.\compile.ps1 [-NoPause]

param(
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Invoke-Pause {
    if (-not $NoPause) {
        Read-Host 'Press Enter to continue'
    }
}

Write-Host '[INFO] Starting compile.ps1...'

# 检查 uv
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host '[ERROR] uv not found. Run init.ps1 first.'
    Invoke-Pause
    exit 1
}

# 确保依赖已安装（含 pyinstaller）
Write-Host '[INFO] Installing PyInstaller...'
& uv add --dev pyinstaller 2>&1 | Out-Null
& uv sync 2>&1 | Out-Null

# 所有需要显式声明的隐藏导入（uv 项目 src-layout 结构）
$hiddenImports = @(
    'easy_tts.config',
    'easy_tts.secret',
    'easy_tts.log',
    'easy_tts.hotkey',
    'easy_tts.hotkey.parser',
    'easy_tts.hotkey.native',
    'easy_tts.identity',
    'easy_tts.qt_lifecycle',
    'easy_tts.audio.capture',
    'easy_tts.audio.player',
    'easy_tts.audio.router',
    'easy_tts.audio.driver',
    'easy_tts.tts.constants',
    'easy_tts.tts.engine',
    'easy_tts.tts.registry',
    'easy_tts.tts.labels',
    'easy_tts.tts.cartesia',
    'easy_tts.ui.overlay',
    'easy_tts.ui.overlay_widgets',
    'easy_tts.ui.settings',
    'easy_tts.ui.settings_state',
    'easy_tts.ui.settings_widgets',
    'easy_tts.ui.settings_style',
    'easy_tts.ui.tray',
    'easy_tts.ui.theme',
    'easy_tts.ui.window'
)

$hiddenImportArgs = @()
foreach ($mod in $hiddenImports) {
    $hiddenImportArgs += '--hidden-import'
    $hiddenImportArgs += $mod
}

Write-Host '[INFO] Running PyInstaller...'
$pyinstallerArgs = @(
    '--noconfirm',
    '--clean',
    '--onefile',
    '--windowed',
    '--name', 'WavTrans',
    '--collect-all', 'cartesia',
    '--collect-all', 'keyring',
    '--collect-submodules', 'websockets'
) + $hiddenImportArgs + @('src/easy_tts/main.py')

& uv run pyinstaller @pyinstallerArgs
$buildExit = $LASTEXITCODE

Write-Host ''

if ($buildExit -ne 0) {
    Write-Host "[ERROR] PyInstaller exited with code $buildExit"
    Invoke-Pause
    exit $buildExit
}

# 清理
Write-Host '[INFO] Cleaning build artifacts...'
$buildDir = Join-Path $PSScriptRoot 'build'
if (Test-Path -LiteralPath $buildDir -PathType Container) {
    Remove-Item -LiteralPath $buildDir -Recurse -Force
}
$specFile = Join-Path $PSScriptRoot 'WavTrans.spec'
if (Test-Path -LiteralPath $specFile -PathType Leaf) {
    Remove-Item -LiteralPath $specFile -Force
}

$outputPath = Join-Path $PSScriptRoot 'dist\WavTrans.exe'
Write-Host "[INFO] Build succeeded: $outputPath"

Invoke-Pause
exit 0
