# Wordy 统一入口：.\wordy.ps1 [init|dev|build] [-Clean] [-NoPause] [-PythonVersion 3.12]
# 保存为 UTF-8 BOM，兼容 cmd 入口使用的 Windows PowerShell 5.1。
# 默认 dev；-Clean 仅用于 build，-NoPause 用于 init/build 的非交互执行。
param(
    [Parameter(Position = 0)]
    [ValidateSet('init', 'dev', 'build')]
    [string]$Action = 'dev',
    [switch]$Clean,
    [switch]$NoPause,
    [string]$PythonVersion = '3.12'
)

$ErrorActionPreference = 'Stop'

function Invoke-Pause {
    if ($Action -ne 'dev' -and -not $NoPause) {
        Read-Host 'Press Enter to continue'
    }
}

# 共用退出码检查，确保 pin/sync 失败时不会继续启动或打包。
function Invoke-Uv {
    & $uvCommand @args
    $commandExitCode = $LASTEXITCODE
    if ($commandExitCode -ne 0) {
        Write-Host "[ERROR] uv exited with code $commandExitCode"
        Invoke-Pause
        exit $commandExitCode
    }
}

# 使用脚本所在目录解析资源；从 cmd 或其他目录启动时也能找到项目，结束后还原目录。
Push-Location -LiteralPath $PSScriptRoot
try {
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uvCommand) {
        throw 'uv not found. Install uv first; see README.md.'
    }

    switch ($Action) {
        'init' {
            Invoke-Uv --version
            if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.python-version'))) {
                Invoke-Uv python pin $PythonVersion
            }
            Write-Host '[INFO] Syncing dependencies...'
            Invoke-Uv sync
            Write-Host '[INFO] Environment ready. Run: .\wordy.ps1 dev'
        }
        'dev' {
            Write-Host '[dev] Starting Wordy...'
            Invoke-Uv run python -m wordy
        }
        'build' {
            $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
            Write-Host '[INFO] Syncing dependencies...'
            Invoke-Uv sync --group dev

            # 保留原打包显式导入和资源列表，合并入口不改变发布产物。
            $hiddenImports = @(
                'wordy.config', 'wordy.secret', 'wordy.log',
                'wordy.hotkey', 'wordy.hotkey.parser', 'wordy.hotkey.native',
                'wordy.identity', 'wordy.qt_lifecycle',
                'wordy.audio.capture', 'wordy.audio.player', 'wordy.audio.router', 'wordy.audio.driver',
                'wordy.tts.constants', 'wordy.tts.engine', 'wordy.tts.registry',
                'wordy.tts.labels', 'wordy.tts.cartesia',
                'wordy.ui.overlay', 'wordy.ui.overlay_widgets', 'wordy.ui.settings',
                'wordy.ui.settings_state', 'wordy.ui.settings_widgets', 'wordy.ui.settings_style',
                'wordy.ui.tray', 'wordy.ui.theme', 'wordy.ui.window'
            )
            $hiddenImportArgs = @()
            foreach ($module in $hiddenImports) {
                $hiddenImportArgs += '--hidden-import', $module
            }
            $pyinstallerArgs = @(
                '-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed',
                '--name', 'Wordy', '--icon', (Join-Path $PSScriptRoot 'wavtrans.ico'),
                '--add-data', 'src/wordy/ui/icons/settings.svg;wordy/ui/icons',
                '--collect-all', 'cartesia', '--collect-all', 'keyring',
                '--collect-submodules', 'websockets'
            )
            if ($Clean) { $pyinstallerArgs += '--clean' }
            $pyinstallerArgs += $hiddenImportArgs
            $pyinstallerArgs += 'src/wordy/__main__.py'

            # Windows 下 uv 的 pyinstaller.exe trampoline 解析路径失败，继续使用模块入口。
            Write-Host '[INFO] Running PyInstaller...'
            Invoke-Uv run python @pyinstallerArgs

            # 清理范围限定为当前项目根目录的构建产物，不递归处理外部路径。
            $buildDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'build'))
            if ([IO.Path]::GetDirectoryName($buildDir) -ne $PSScriptRoot) {
                throw 'Build cleanup target outside project root.'
            }
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
