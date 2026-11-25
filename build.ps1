param(
    [switch]$OneDir,
    [switch]$AsInvoker,
    [string]$DistPath = 'dist'
)

$ErrorActionPreference = 'Stop'
$buildOriginalPath = $env:PATH
Push-Location -LiteralPath $PSScriptRoot
try {
    $pythonExe = if (Test-Path -LiteralPath '.venv\Scripts\python.exe') {
        (Resolve-Path -LiteralPath '.venv\Scripts\python.exe').Path
    } else {
        (Get-Command python -ErrorAction Stop).Source
    }
    # Qt imports the Windows ICU API. An unrelated toolkit on PATH can supply an
    # incompatible icuuc.dll to PyInstaller's binary dependency analysis.
    $buildSearchPaths = @((Join-Path $env:SystemRoot 'System32'), $env:SystemRoot, (Split-Path -Parent $pythonExe))
    $env:PATH = $buildSearchPaths -join [IO.Path]::PathSeparator
    $mode = if ($OneDir) { '--onedir' } else { '--onefile' }
    & $pythonExe -m scripts.prepare_release 'build/metadata'
    if ($LASTEXITCODE -ne 0) { throw "Release metadata generation failed with exit code $LASTEXITCODE" }
    $buildArgs = @('-m', 'PyInstaller', '--noconfirm', '--clean', $mode, '--windowed',
                   '--distpath', $DistPath, '--icon', 'assets/steam-dns-optimizer.ico',
                   '--version-file', 'build/metadata/version.txt', '--add-data', 'assets:assets',
                   '--add-data', 'build/metadata/ThirdPartyNotices.zip:.')
    if (!$AsInvoker) { $buildArgs += '--uac-admin' }
    $buildArgs += @('--collect-data', 'qfluentwidgets', '--name', 'SteamDNSOptimizer', 'main.py')
    & $pythonExe @buildArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
} finally {
    $env:PATH = $buildOriginalPath
    Pop-Location
}
