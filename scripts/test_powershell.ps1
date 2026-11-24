$ErrorActionPreference = 'Stop'
$executable = (Get-Process -Id $PID).Path
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('jumpseat test ' + [guid]::NewGuid().ToString('N'))
$runner = $null

function Assert($Condition, $Message) {
    if (-not $Condition) {
        throw $Message
    }
}

function Write-Utf8($Path, $Text) {
    [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($true))
}

function Read-JsonEventually($Path) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt 15) {
        if (Test-Path -LiteralPath $Path) {
            $value = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
            if ($null -ne $value) {
                return $value
            }
        }
        if ($runner.HasExited) {
            throw "Runner exited: $($runner.StandardError.ReadToEnd())"
        }
        Start-Sleep -Milliseconds 100
    }
    throw "No JSON result at $Path"
}

function Invoke-TestRequest($Command, $ExpectedCode, $ExpectedOutput, $Timeout = 5, $Session = $state.session,
                            $Expires = ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + 10),
                            [switch]$CancelBeforeStart, [switch]$CancelWhileRunning) {
    $job = Join-Path (Join-Path $temporary 'jobs') ([guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $job | Out-Null
    Write-Utf8 (Join-Path $job 'command.ps1') $Command
    $request = @{ session = $Session; timeout = $Timeout; expires = $Expires } | ConvertTo-Json
    if ($CancelBeforeStart) {
        New-Item -ItemType File -Path (Join-Path $job 'cancel') | Out-Null
    }
    Write-Utf8 (Join-Path $job 'request.tmp') $request
    Move-Item -LiteralPath (Join-Path $job 'request.tmp') -Destination (Join-Path $job 'ready.json')
    if ($CancelWhileRunning) {
        $null = Read-JsonEventually (Join-Path $job 'running.json')
        Start-Sleep -Seconds 1
        New-Item -ItemType File -Path (Join-Path $job 'cancel') | Out-Null
    }
    $result = Read-JsonEventually (Join-Path $job 'result.json')
    Assert ($result.exit_code -eq $ExpectedCode) "Wrong exit code: $($result | ConvertTo-Json)"
    $text = $result.error
    if (Test-Path -LiteralPath (Join-Path $job 'output.log')) {
        $text += Get-Content -LiteralPath (Join-Path $job 'output.log') -Raw
    }
    Assert ($text -match $ExpectedOutput) "Missing '$ExpectedOutput' in '$text'"
    Write-Host "ok: $Command"
}

try {
    foreach ($name in 'Start-Jumpseat.ps1', 'Invoke-JumpseatCommand.ps1') {
        $tokens = $null
        $errors = $null
        $null = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $name),
                                                                  [ref]$tokens, [ref]$errors)
        Assert ($errors.Count -eq 0) ($errors | Out-String)
    }
    New-Item -ItemType Directory -Path $temporary | Out-Null
    $startup = "& '$(Join-Path $PSScriptRoot 'Start-Jumpseat.ps1')' -ExchangeDirectory '$temporary'"
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        # Linux tests exercise cancellation using a stand-in; Windows taskkill /T still needs a lab test.
        $startup = @'
function global:taskkill.exe {
    if ($args[0] -ne '/PID' -or $args[2] -ne '/T' -or $args[3] -ne '/F') {
        throw 'Unexpected taskkill arguments'
    }
    Stop-Process -Id ([int]$args[1]) -Force
}
'@ + "`n" + $startup
    }
    $launcher = Join-Path $temporary 'launch.ps1'
    Write-Utf8 $launcher $startup
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $executable
    $info.Arguments = '-NoProfile -File "{0}"' -f $launcher
    $info.UseShellExecute = $false
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $runner = [Diagnostics.Process]::Start($info)
    $runnerOutput = $runner.StandardOutput.ReadToEndAsync()
    $runnerErrors = $runner.StandardError.ReadToEndAsync()
    $runner.StandardInput.WriteLine('START')
    $state = Read-JsonEventually (Join-Path $temporary 'runner.json')

    Invoke-TestRequest "'hello'; [char]0x00E6; Write-Warning 'visible warning'" 0 'visible warning'
    $firstLog = Get-ChildItem -LiteralPath (Join-Path $temporary 'jobs') -Recurse -Filter output.log
    Assert ((Get-Content -LiteralPath $firstLog.FullName -Raw) -match ([char]0x00E6)) 'Unicode output lost'
    Invoke-TestRequest "'partial'; throw 'deliberate failure'" 1 'deliberate failure'
    Invoke-TestRequest "Write-Error 'nonterminating failure'" 1 'nonterminating failure'
    Invoke-TestRequest 'Read-Host "must not prompt"' 1 'NonInteractive'
    Invoke-TestRequest 'exit 7' 7 'code 7'
    Invoke-TestRequest ("& '$executable' -NonInteractive -Command 'exit 9'") 9 'code 9'
    Invoke-TestRequest "'must not run'" 1 'previous runner session' -Session 'old-session'
    Invoke-TestRequest "'must not run'" 1 'expired' -Expires 0
    Invoke-TestRequest "'must not run'" 1 'cancelled before' -CancelBeforeStart
    Invoke-TestRequest 'Start-Sleep -Seconds 30' 1 'cancelled by the client' -CancelWhileRunning
    Invoke-TestRequest 'Start-Sleep -Seconds 30' 1 'exceeded' -Timeout 1
    Invoke-TestRequest "'still alive'" 0 'still alive'
    Write-Host 'PowerShell runner tests passed.'
}
finally {
    if ($null -ne $runner) {
        if (-not $runner.HasExited) {
            if ($PSVersionTable.PSVersion.Major -ge 7) {
                $runner.Kill($true)
            }
            else {
                & taskkill.exe /PID $runner.Id /T /F
            }
            $runner.WaitForExit()
        }
        $runner.Dispose()
    }
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary -Recurse -Force
    }
}
