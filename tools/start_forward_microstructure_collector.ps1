param(
    [string]$Python = "python",
    [string]$Database = "data/forward_microstructure/forward-metadata.sqlite3",
    [string]$RawPartitions = "data/forward_microstructure/raw",
    [string]$Venues = "BINANCE,OKX,BINGX",
    [string]$Symbols = "BTCUSDT,ETHUSDT,SOLUSDT"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtime = Join-Path $root "data/forward_microstructure"
$logs = Join-Path $runtime "logs"
$pidPath = Join-Path $runtime "collector.pid"
New-Item -ItemType Directory -Force -Path $logs | Out-Null

# A script launched by path otherwise exposes tools/ rather than the repository
# root as Python's import base. Start-Process inherits this scoped environment.
$existingPythonPath = $env:PYTHONPATH
$pythonPaths = @($root)
$localDependencies = Join-Path $root ".codex-test-deps"
if (Test-Path -LiteralPath $localDependencies) {
    $pythonPaths += $localDependencies
}
if ($existingPythonPath) {
    $pythonPaths += $existingPythonPath
}
$env:PYTHONPATH = $pythonPaths -join [IO.Path]::PathSeparator
$env:FORWARD_COLLECTION_ENABLED = "true"

if (Test-Path -LiteralPath $pidPath) {
    $existingPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    if (Get-Process -Id $existingPid -ErrorAction SilentlyContinue) {
        throw "Forward collector is already running as PID $existingPid"
    }
}

$arguments = @(
    "-u", "tools/run_forward_microstructure_collector.py",
    "--database", $Database,
    "--raw-partitions", $RawPartitions,
    "--venues", $Venues,
    "--symbols", $Symbols
)
$process = Start-Process -FilePath $Python -ArgumentList $arguments -WorkingDirectory $root `
    -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "collector.stdout.log") `
    -RedirectStandardError (Join-Path $logs "collector.stderr.log") -PassThru
Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ascii
Write-Output "Forward microstructure collector started as PID $($process.Id)"
