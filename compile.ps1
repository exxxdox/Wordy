param(
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'

Write-Host '[INFO] Starting compile.ps1...'

Set-Location -LiteralPath $PSScriptRoot

function Invoke-Pause {
    if (-not $NoPause) {
        Read-Host 'Press Enter to continue'
    }
}

$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    Write-Host '[ERROR] Cannot find .venv\Scripts\python.exe'
    Invoke-Pause
    exit 1
}

Write-Host '[INFO] Upgrading PyInstaller in the virtual environment...'
& $pythonPath -m pip install --upgrade pyinstaller
$pipExit = $LASTEXITCODE
if ($pipExit -ne 0) {
    Write-Host "[ERROR] pip install pyinstaller failed with code $pipExit"
    Invoke-Pause
    exit $pipExit
}

Write-Host '[INFO] Running PyInstaller to build dist\WavTrans.exe...'
$pyinstallerArgs = @(
    '-m', 'PyInstaller',
    '--noconfirm',
    '--clean',
    '--onefile',
    '--windowed',
    '--name', 'WavTrans',
    '--collect-all', 'cartesia',
    '--collect-all', 'keyring',
    '--collect-submodules', 'websockets',
    '--hidden-import', 'log_stream',
    'main.py'
)
& $pythonPath @pyinstallerArgs
$buildExit = $LASTEXITCODE

Write-Host ''

if ($buildExit -ne 0) {
    Write-Host "[ERROR] PyInstaller exited with code $buildExit"
    Invoke-Pause
    exit $buildExit
}

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
