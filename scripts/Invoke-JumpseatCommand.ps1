param(
    [Parameter(Mandatory = $true)][string]$JobDirectory
)

$ErrorActionPreference = 'Stop'
$log = [IO.StreamWriter]::new((Join-Path $JobDirectory 'output.log'), $false, [Text.UTF8Encoding]::new($true))
$log.AutoFlush = $true
$exitCode = 0
try {
    $LASTEXITCODE = 0
    & (Join-Path $JobDirectory 'command.ps1') *>&1 |
        Out-String -Stream -Width 250 |
        ForEach-Object {
            Write-Host $_
            $log.WriteLine($_)
        }
    if ($LASTEXITCODE -ne 0) {
        $exitCode = $LASTEXITCODE
    }
}
catch {
    $message = 'ERROR: ' + $_.ToString()
    Write-Host $message -ForegroundColor Red
    $log.WriteLine($message)
    $exitCode = 1
}
finally {
    $log.Dispose()
}
exit $exitCode
