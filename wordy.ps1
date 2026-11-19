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

function Invoke-Step {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    $commandExitCode = $LASTEXITCODE
    if ($commandExitCode -ne 0) {
        Write-Host "[ERROR] $Command exited with code $commandExitCode"
        Invoke-Pause
        exit $commandExitCode
    }
}

function Invoke-Cargo {
    Invoke-Step $cargoCommand $args
}

Push-Location -LiteralPath $PSScriptRoot
try {
    $cargoCommand = Get-Command cargo -ErrorAction SilentlyContinue
    if (-not $cargoCommand) {
        # Explorer may start with a stale PATH right after installing Rust.
        $fallback = Join-Path $env:USERPROFILE '.cargo\bin\cargo.exe'
        if (Test-Path -LiteralPath $fallback) {
            $cargoCommand = $fallback
        } else {
            throw 'cargo not found. Install the Rust toolchain first.'
        }
    }
    $pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
    if (-not $pnpm) {
        throw 'pnpm not found. Install pnpm before building the settings UI.'
    }
    $frontend = Join-Path $PSScriptRoot 'node_modules'
    $crate = Join-Path $PSScriptRoot 'src-tauri'

    switch ($Action) {
        'init' {
            Invoke-Cargo --version
            Write-Host '[INFO] Fetching Rust dependencies...'
            Invoke-Cargo fetch --manifest-path (Join-Path $crate 'Cargo.toml')
            Write-Host '[INFO] Installing frontend dependencies...'
            Invoke-Step $pnpm.Source @('install', '--allow-build=esbuild')
            Write-Host '[INFO] Environment ready. Run: .\wordy.ps1 dev'
        }
        'dev' {
            if (-not (Test-Path -LiteralPath $frontend)) {
                Invoke-Step $pnpm.Source @('install', '--allow-build=esbuild')
            }
            # 上次未退出的 Vite 会占住 1420，Tauri 随后报一个难读的 dev server 错误。
            $busy = Get-NetTCPConnection -LocalPort 1420 -State Listen -ErrorAction SilentlyContinue
            if ($busy) {
                $owners = ($busy | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '
                throw "端口 1420 已被进程 $owners 占用。结束该进程后重试（通常是上次未退出的 Vite）。"
            }
            Write-Host '[dev] Starting Wordy...'
            Invoke-Step $cargoCommand.Source @('tauri', 'dev')
        }
        'build' {
            $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
            if (-not (Test-Path -LiteralPath $frontend)) {
                Invoke-Step $pnpm.Source @('install', '--allow-build=esbuild')
            }
            Write-Host '[INFO] Building settings UI...'
            Invoke-Step $pnpm.Source @('build')
            $target = Join-Path $crate 'target'
            if ($Clean) { Invoke-Cargo clean --manifest-path (Join-Path $crate 'Cargo.toml') }
            Write-Host '[INFO] Building release...'
            Invoke-Cargo build --manifest-path (Join-Path $crate 'Cargo.toml') --release --features packaged
            $distDir = Join-Path $PSScriptRoot 'dist'
            New-Item -ItemType Directory -Force -Path $distDir | Out-Null
            $sourceExe = Join-Path $target 'release\wordy.exe'
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
    # A double-clicked dev session must not close before the error is readable.
    if (-not $NoPause) { Read-Host 'Press Enter to continue' }
    exit 1
} finally {
    Pop-Location
}
exit 0
