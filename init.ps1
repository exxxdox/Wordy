param(
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'

Write-Host '[INFO] Starting init.ps1...'

Set-Location -LiteralPath $PSScriptRoot

function Invoke-Pause {
    if (-not $NoPause) {
        Read-Host 'Press Enter to continue'
    }
}

$venvPath = Join-Path $PSScriptRoot '.venv'
$pythonPath = Join-Path $venvPath 'Scripts\python.exe'
$requirementsPath = Join-Path $PSScriptRoot 'requirements.txt'

if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
    Write-Host '[ERROR] Cannot find requirements.txt'
    Invoke-Pause
    exit 1
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    Write-Host '[INFO] Creating Python virtual environment in .venv...'

    $systemPython = Get-Command py -ErrorAction SilentlyContinue
    if ($systemPython) {
        & py -3 -m venv $venvPath
    }
    else {
        $systemPython = Get-Command python -ErrorAction SilentlyContinue
        if (-not $systemPython) {
            Write-Host '[ERROR] Cannot find Python. Please install Python 3 and try again.'
            Invoke-Pause
            exit 1
        }

        & python -m venv $venvPath
    }

    $venvExit = $LASTEXITCODE
    if ($venvExit -ne 0) {
        Write-Host "[ERROR] Failed to create virtual environment with code $venvExit"
        Invoke-Pause
        exit $venvExit
    }
}
else {
    Write-Host '[INFO] Found existing .venv virtual environment.'
}

# Write-Host '[INFO] Upgrading pip...'
# & $pythonPath -m pip install --upgrade pip
# $pipUpgradeExit = $LASTEXITCODE
# if ($pipUpgradeExit -ne 0) {
#     Write-Host "[ERROR] pip upgrade failed with code $pipUpgradeExit"
#     Invoke-Pause
#     exit $pipUpgradeExit
# }

Write-Host '[INFO] Installing dependencies from requirements.txt...'
& $pythonPath -m pip install -r $requirementsPath
$installExit = $LASTEXITCODE
if ($installExit -ne 0) {
    Write-Host "[ERROR] Dependency installation failed with code $installExit"
    Invoke-Pause
    exit $installExit
}

Write-Host '[INFO] Environment initialization completed successfully.'

Invoke-Pause

exit 0
