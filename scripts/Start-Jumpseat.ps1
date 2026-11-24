param(
    [string]$ExchangeDirectory = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'
$root = (Get-Item -LiteralPath $ExchangeDirectory).FullName
$lock = [IO.File]::Open((Join-Path $root '.runner.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
$session = [guid]::NewGuid().ToString('N')
$process = $null
$clock = [Diagnostics.Stopwatch]::StartNew()
$lastHeartbeat = -2

function Write-JsonFile($Path, $Value) {
    $temporary = $Path + '.tmp'
    [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($true))
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Update-Heartbeat {
    if (($clock.Elapsed.TotalSeconds - $script:lastHeartbeat) -ge 1) {
        Write-JsonFile (Join-Path $root 'runner.json') @{
            session = $session
            computer = [Environment]::MachineName
            user = [Environment]::UserDomainName + '\' + [Environment]::UserName
            heartbeat = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
        }
        $script:lastHeartbeat = $clock.Elapsed.TotalSeconds
    }
}

function Stop-Command {
    if ($null -ne $script:process -and -not $script:process.HasExited) {
        & taskkill.exe /PID $script:process.Id /T /F
        $script:process.Refresh()
        if (-not $script:process.WaitForExit(5000)) {
            throw "Could not stop command process $($script:process.Id). Stop it manually."
        }
    }
}

try {
    New-Item -ItemType Directory -Path (Join-Path $root 'jobs') -Force | Out-Null
    Write-Host "Jumpseat runner on $([Environment]::MachineName) as $([Environment]::UserDomainName)\$([Environment]::UserName)"
    Write-Host "Anyone who can write to $root can run commands with your permissions."
    Write-Host 'Only continue with SOC approval. Scripts and logs remain on disk. Ctrl+C stops the runner.'
    if ((Read-Host 'Type START to enable command execution') -cne 'START') {
        throw 'Runner not enabled.'
    }
    while ($true) {
        Update-Heartbeat
        foreach ($directory in Get-ChildItem -LiteralPath (Join-Path $root 'jobs') -Directory) {
            $ready = Join-Path $directory.FullName 'ready.json'
            if (-not (Test-Path -LiteralPath $ready)) {
                continue
            }
            $request = Get-Content -LiteralPath $ready -Raw | ConvertFrom-Json
            $errorMessage = ''
            $exitCode = 1
            Move-Item -LiteralPath $ready -Destination (Join-Path $directory.FullName 'running.json')
            try {
                if ($request.session -cne $session) {
                    throw 'Request belongs to a previous runner session; it was not executed.'
                }
                if ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0 -gt $request.expires) {
                    throw 'Request expired before execution.'
                }
                if ($request.timeout -isnot [long] -and $request.timeout -isnot [int]) {
                    throw 'Timeout must be an integer.'
                }
                if ($request.timeout -lt 1 -or $request.timeout -gt 3600) {
                    throw 'Timeout must be between 1 and 3600 seconds.'
                }
                if (Test-Path -LiteralPath (Join-Path $directory.FullName 'cancel')) {
                    throw 'Request cancelled before execution.'
                }
                Write-Host "`n--- $($directory.Name) ---"
                Get-Content -LiteralPath (Join-Path $directory.FullName 'command.ps1') | Out-Host
                $arguments = '-NoProfile -NonInteractive -File "{0}" -JobDirectory "{1}"' -f (
                    (Join-Path $PSScriptRoot 'Invoke-JumpseatCommand.ps1'), $directory.FullName)
                $process = Start-Process -FilePath (Get-Process -Id $PID).Path `
                    -ArgumentList $arguments -NoNewWindow -PassThru
                $null = $process.Handle
                $timer = [Diagnostics.Stopwatch]::StartNew()
                while (-not $process.WaitForExit(200)) {
                    Update-Heartbeat
                    if ($timer.Elapsed.TotalSeconds -ge $request.timeout) {
                        throw "Command exceeded $($request.timeout) seconds."
                    }
                    if (Test-Path -LiteralPath (Join-Path $directory.FullName 'cancel')) {
                        throw 'Command cancelled by the client.'
                    }
                }
                $commandExitCode = $process.ExitCode
                if ($null -eq $commandExitCode) {
                    throw 'Command ended without an exit code.'
                }
                $exitCode = $commandExitCode
                if ($exitCode -ne 0) {
                    $errorMessage = "Command exited with code $exitCode."
                }
            }
            catch {
                $errorMessage = $_.ToString()
                Write-Host $errorMessage -ForegroundColor Red
            }
            finally {
                Stop-Command
                if ($null -ne $process) {
                    $process.Dispose()
                    $process = $null
                }
            }
            Write-JsonFile (Join-Path $directory.FullName 'result.json') @{
                exit_code = $exitCode
                error = $errorMessage
            }
        }
        Start-Sleep -Milliseconds 200
    }
}
finally {
    try {
        Stop-Command
        $state = Join-Path $root 'runner.json'
        if (Test-Path -LiteralPath $state) {
            Remove-Item -LiteralPath $state -Force
        }
    }
    finally {
        if ($null -ne $process) {
            $process.Dispose()
        }
        $lock.Dispose()
    }
}
