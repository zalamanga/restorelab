$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')
$restoreRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location -LiteralPath $restoreRoot
try {
    Invoke-RestoreNative python -m unittest discover -s tests -p 'test_*.py'
} finally { Pop-Location }
