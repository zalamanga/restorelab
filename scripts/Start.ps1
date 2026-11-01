param([int]$Port = 8140, [int]$MaxRuns = 5)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')
$restoreRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ($Port -lt 1024 -or $Port -gt 65535 -or $MaxRuns -lt 1 -or $MaxRuns -gt 50) { throw 'Port or MaxRuns is outside its allowed range.' }
$restoreLocal = Join-Path $restoreRoot '.local'
$restoreRuntime = Join-Path $restoreRoot '.runtime'
New-Item -ItemType Directory -Path $restoreLocal, $restoreRuntime -Force | Out-Null
$restorePidFile = Join-Path $restoreRuntime 'server.json'
$restoreRunFile = Join-Path $restoreRoot 'run.py'
if (Test-Path -LiteralPath $restorePidFile) {
    $restoreOld = Get-Content -LiteralPath $restorePidFile -Raw | ConvertFrom-Json
    try {
        $restoreHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$($restoreOld.port)/health" -TimeoutSec 2
        $restoreOldProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($restoreOld.pid)"
        if ($restoreHealth.app -eq 'restorelab' -and $restoreHealth.process_id -eq $restoreOld.pid -and $restoreOldProcess.CommandLine.Contains($restoreRunFile)) {
            Write-Output "RestoreLab already running: http://127.0.0.1:$($restoreOld.port)"
            return
        }
    } catch { }
}
$restorePython = (Get-Command python -ErrorAction Stop).Source
try { $restoreDocker = Invoke-RestoreNative docker info --format '{{.OSType}}' } catch { throw 'Start Docker Desktop with Linux containers before starting RestoreLab.' }
if ($restoreDocker -ne 'linux') { throw 'Start Docker Desktop with Linux containers before starting RestoreLab.' }
Invoke-RestoreNative $restorePython (Join-Path $restoreRoot 'infrastructure\bootstrap.py')
$restoreArguments = @('"' + $restoreRunFile + '"', '--port', "$Port", '--max-runs', "$MaxRuns")
$restoreOldTemp = $env:TEMP
$restoreOldTmp = $env:TMP
try {
    $env:TEMP = Join-Path $restoreLocal 'tmp'
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
    $restoreProcess = Start-Process -FilePath $restorePython -ArgumentList $restoreArguments -WorkingDirectory $restoreRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $restoreLocal 'server.stdout.log') -RedirectStandardError (Join-Path $restoreLocal 'server.stderr.log') -PassThru
} finally {
    $env:TEMP = $restoreOldTemp
    $env:TMP = $restoreOldTmp
}
for ($restoreAttempt = 0; $restoreAttempt -lt 60; $restoreAttempt++) {
    $restoreProcess.Refresh()
    if ($restoreProcess.HasExited) { throw 'RestoreLab exited during startup. Inspect .local/server.stderr.log.' }
    try {
        $restoreHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 1
        if ($restoreHealth.app -eq 'restorelab' -and $restoreHealth.process_id -eq $restoreProcess.Id) {
            $restoreMetadata = @{pid=$restoreProcess.Id;port=$Port;root=$restoreRoot;started_at=$restoreProcess.StartTime.ToUniversalTime().ToString('o')}
            [IO.File]::WriteAllText($restorePidFile, ($restoreMetadata | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
            Write-Output "RestoreLab: http://127.0.0.1:$Port"
            Write-Output 'Only synthetic lab data is used. No drill runs until you start one in the dashboard.'
            return
        }
    } catch { }
    Start-Sleep -Milliseconds 250
}
throw 'RestoreLab readiness timed out. Inspect .local/server.stderr.log.'
