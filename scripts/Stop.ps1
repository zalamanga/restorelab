param([switch]$Interrupt)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')
$restoreRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$restorePidFile = Join-Path $restoreRoot '.runtime\server.json'
if (-not (Test-Path -LiteralPath $restorePidFile)) { throw 'No owned RestoreLab server record was found.' }
$restoreMetadata = Get-Content -LiteralPath $restorePidFile -Raw | ConvertFrom-Json
$restoreOwned = Get-CimInstance Win32_Process -Filter "ProcessId = $($restoreMetadata.pid)"
$restoreRunFile = Join-Path $restoreRoot 'run.py'
if ($restoreOwned) {
    if ($restoreMetadata.root -ne $restoreRoot -or -not $restoreOwned.CommandLine.Contains($restoreRunFile)) { throw 'Process identity mismatch; no process was stopped.' }
    $restoreProcess = Get-Process -Id $restoreMetadata.pid
    if ($restoreProcess.StartTime.ToUniversalTime().ToString('o') -ne $restoreMetadata.started_at) { throw 'Process start time mismatch; no process was stopped.' }
    $restoreState = $null
    try { $restoreState = Invoke-RestMethod -Uri "http://127.0.0.1:$($restoreMetadata.port)/api/state" -TimeoutSec 2 } catch { }
    if ($restoreState.lab.active_run_id -and -not $Interrupt) { throw 'A drill is active. Wait for completion, or use -Interrupt to stop the controller and retain its unfinished evidence.' }
    Stop-Process -Id $restoreMetadata.pid -ErrorAction Stop
}
Invoke-RestoreNative docker compose --project-directory $restoreRoot --project-name restorelab -f (Join-Path $restoreRoot 'compose.yaml') --profile recovery stop
Write-Output 'RestoreLab stopped. Controller history, backups, WAL and recovery volumes were preserved.'
