# 让脚本在可恢复错误时停止执行，便于保留正确的退出码。
$ErrorActionPreference = 'Stop'

Write-Host '[INFO] Starting run.ps1...'

# 切换到当前 run.ps1 所在目录；$PSScriptRoot 等价于批处理里的 %~dp0。
Set-Location -LiteralPath $PSScriptRoot

$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
# 检查项目根目录下是否存在 Python 虚拟环境解释器。
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    Write-Host '[ERROR] Cannot find .venv\Scripts\python.exe'
    # 暂停窗口，方便双击运行时看到错误信息。
    Read-Host 'Press Enter to continue'
    exit 1
}


# 使用虚拟环境里的 Python 解释器运行项目入口 main.py。
& $pythonPath 'main.py'
$exitCode = $LASTEXITCODE

Write-Host ''

if ($exitCode -ne 0) {
    Write-Host "[ERROR] main.py exited with code $exitCode"
}

# 暂停窗口，方便双击运行后查看程序输出。
Read-Host 'Press Enter to continue'

# 使用 main.py 的退出码退出 run.ps1，方便其他脚本或工具判断运行结果。
exit $exitCode
