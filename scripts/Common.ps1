function Invoke-RestoreNative {
    $restoreExecutable = [string]$args[0]
    $restoreNativeArguments = @($args | Select-Object -Skip 1)
    $restorePreviousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $restoreExecutable @restoreNativeArguments
        $restoreNativeExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $restorePreviousPreference
    }
    if ($restoreNativeExit -ne 0) { throw "Native command failed (exit $restoreNativeExit)." }
}
