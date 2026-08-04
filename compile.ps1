# Easy TTS 打包脚本（uv 版）
# 用法：.\compile.ps1 [-Clean] [-Sync] [-NoPause]
#
#   -Clean   清理 PyInstaller 缓存，强制全量重编译
#   -NoPause 编译结束后不等待按键

param(
    [switch]$Clean,
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

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

Write-Host '[INFO] Syncing dependencies...'
& uv sync --group dev
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] uv sync failed (exit code $LASTEXITCODE)"
    Invoke-Pause
    exit $LASTEXITCODE
}

# 所有需要显式声明的隐藏导入（uv 项目 src-layout 结构）
$hiddenImports = @(
    'wordy.config',
    'wordy.secret',
    'wordy.log',
    'wordy.hotkey',
    'wordy.hotkey.parser',
    'wordy.hotkey.native',
    'wordy.identity',
    'wordy.qt_lifecycle',
    'wordy.audio.capture',
    'wordy.audio.player',
    'wordy.audio.router',
    'wordy.audio.driver',
    'wordy.tts.constants',
    'wordy.tts.engine',
    'wordy.tts.registry',
    'wordy.tts.labels',
    'wordy.tts.cartesia',
    'wordy.ui.overlay',
    'wordy.ui.overlay_widgets',
    'wordy.ui.settings',
    'wordy.ui.settings_state',
    'wordy.ui.settings_widgets',
    'wordy.ui.settings_style',
    'wordy.ui.tray',
    'wordy.ui.theme',
    'wordy.ui.window'
)

$hiddenImportArgs = @()
foreach ($mod in $hiddenImports) {
    $hiddenImportArgs += '--hidden-import'
    $hiddenImportArgs += $mod
}

Write-Host '[INFO] Running PyInstaller...'
$iconPath = Join-Path $PSScriptRoot 'wavtrans.ico'

$pyinstallerArgs = @(
    '--noconfirm',
    '--onefile',
    '--windowed',
    '--name', 'Wordy',
    '--icon', $iconPath,
    '--add-data', 'src/wordy/ui/icons/settings.svg;wordy/ui/icons',
    '--collect-all', 'cartesia',
    '--collect-all', 'keyring',
    '--collect-submodules', 'websockets'
) + $(if ($Clean) { @('--clean') } else { @() }) + $hiddenImportArgs + @('src/wordy/__main__.py')

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
$specFile = Join-Path $PSScriptRoot 'Wordy.spec'
if (Test-Path -LiteralPath $specFile -PathType Leaf) {
    Remove-Item -LiteralPath $specFile -Force
}

$stopwatch.Stop()
$elapsed = $stopwatch.Elapsed.ToString('mm\:ss')

$outputPath = Join-Path $PSScriptRoot 'dist\Wordy.exe'

Write-Host "[INFO] Build succeeded in $elapsed : $outputPath"

Invoke-Pause
exit 0
