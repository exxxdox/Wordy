param(
    [Parameter(Mandatory = $true)]
    [string]$FfmpegPath,
    [string]$FfmpegSha256
)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$codec = Get-Item -LiteralPath $FfmpegPath
if ($codec.PSIsContainer -or $codec.Extension -ne '.exe') {
    throw 'Pass the path of an actual FFmpeg executable, not a shim or a directory.'
}
if ($FfmpegSha256 -and (Get-FileHash -LiteralPath $codec.FullName -Algorithm SHA256).Hash -ne $FfmpegSha256) {
    throw 'FFmpeg SHA-256 does not match.'
}
$target = 'x86_64-pc-windows-msvc'
$output = Join-Path $root 'dist/wordy-rust'

Push-Location -LiteralPath $root
try {
    cargo build --locked --release --features packaged --target $target
    if ($LASTEXITCODE -ne 0) { throw "Cargo exited with code $LASTEXITCODE." }
    $executable = Join-Path $root "target/$target/release/wordy.exe"
    $smoke = Start-Process -FilePath $executable -ArgumentList '--smoke-test' -WindowStyle Hidden -PassThru
    if (-not $smoke.WaitForExit(15000)) {
        $smoke.Kill()
        throw 'Wordy smoke test timed out.'
    }
    if ($smoke.ExitCode -ne 0) { throw "Wordy smoke test exited with code $($smoke.ExitCode)." }
    New-Item -ItemType Directory -Force -Path $output | Out-Null
    Copy-Item -LiteralPath $codec.FullName -Destination (Join-Path $output 'ffmpeg.exe')
    Copy-Item -LiteralPath $executable -Destination (Join-Path $output 'Wordy.exe')
    Get-Item -LiteralPath (Join-Path $output 'Wordy.exe')
} finally {
    Pop-Location
}
