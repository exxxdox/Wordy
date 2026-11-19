# Wordy 统一入口：.\wordy.ps1 [init|dev|build] [-Port 1420] [-Clean] [-NoPause]
# 默认 dev；-Port 是 dev 的起始端口，被占用时自动顺延；-Clean 仅用于 build。
param(
    [Parameter(Position = 0)]
    [ValidateSet('init', 'dev', 'build')]
    [string]$Action = 'dev',
    [ValidateRange(1, 65535)]
    [int]$Port = 1420,
    [switch]$Clean,
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'

function Find-FreePort([int]$Start) {
    $limit = [Math]::Min($Start + 50, 65535)
    for ($candidate = $Start; $candidate -lt $limit; $candidate++) {
        $listener = $null
        try {
            $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $candidate)
            $listener.Start()
            $listener.Stop()
            return $candidate
        } catch {
            if ($listener) { try { $listener.Stop() } catch { } }
        }
    }
    throw "从 $Start 起找不到可用端口（已尝试 50 个）"
}

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
            $devPort = Find-FreePort $Port
            if ($devPort -ne $Port) {
                Write-Host "[dev] Port $Port is busy; using $devPort."
            }
            $env:WORDY_DEV_PORT = "$devPort"
            # Tauri 必须和 Vite 使用同一个端口。Windows PowerShell 传参时会丢掉内联
            # JSON 的引号，所以先写到临时文件再交给 --config。
            $configPath = Join-Path $env:TEMP 'wordy-dev-config.json'
            $configJson = '{"build":{"devUrl":"http://127.0.0.1:' + $devPort + '"}}'
            [IO.File]::WriteAllText($configPath, $configJson)
            Write-Host "[dev] Starting Wordy on port $devPort..."
            Invoke-Step $cargoCommand.Source @('tauri', 'dev', '--config', $configPath)
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
