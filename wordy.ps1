# Wordy 统一入口：.\wordy.ps1 [init|dev|build] [-Clean] [-NoPause]
# 默认 dev；-Clean 仅用于 build，-NoPause 用于非交互执行。
param(
    [Parameter(Position = 0)]
    [ValidateSet('init', 'dev', 'build')]
    [string]$Action = 'dev',
    [switch]$Clean,
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'

function Invoke-Pause {
    if ($Action -ne 'dev' -and -not $NoPause) {
        Read-Host 'Press Enter to continue'
    }
}

function Invoke-Cargo {
    & $cargoCommand @args
    $commandExitCode = $LASTEXITCODE
    if ($commandExitCode -ne 0) {
        Write-Host "[ERROR] cargo exited with code $commandExitCode"
        Invoke-Pause
        exit $commandExitCode
    }
}

Push-Location -LiteralPath $PSScriptRoot
try {
    $cargoCommand = Get-Command cargo -ErrorAction SilentlyContinue
    if (-not $cargoCommand) {
        throw 'cargo not found. Install the Rust toolchain first.'
    }

    switch ($Action) {
        'init' {
            Invoke-Cargo --version
            Write-Host '[INFO] Fetching dependencies...'
            Invoke-Cargo fetch
            Write-Host '[INFO] Environment ready. Run: .\wordy.ps1 dev'
        }
        'dev' {
            Write-Host '[dev] Starting Wordy...'
            Invoke-Cargo run
        }
        'build' {
            $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
            if ($Clean) { Invoke-Cargo clean }
            Write-Host '[INFO] Building release...'
            Invoke-Cargo build --release --features packaged
            $distDir = Join-Path $PSScriptRoot 'dist'
            New-Item -ItemType Directory -Force -Path $distDir | Out-Null
            $sourceExe = Join-Path $PSScriptRoot 'target\release\wordy.exe'
            $outputPath = Join-Path $distDir 'Wordy.exe'
            Copy-Item -LiteralPath $sourceExe -Destination $outputPath -Force
            $stopwatch.Stop()
            $elapsed = $stopwatch.Elapsed.ToString('mm\:ss')
            Write-Host "[INFO] Build succeeded in $elapsed : $outputPath"
        }
    }
    Invoke-Pause
} catch {
    Write-Host "[ERROR] $($_.Exception.Message)"
    Invoke-Pause
    exit 1
} finally {
    Pop-Location
}
exit 0
