# `jumpseat.py ps` triggered Microsoft Defender

## What happened

On 2026-11-24, while testing a PowerShell repository issue on `h1-z-esx-tsp01`, `jumpseat.py ps` raised 24 alerts in the
SOC. Defender put the account in containment, which closed the RDP session twice. The SOC (Dan Solberg) named the starter
as the cause:

```text
powershell -NoP -W Hidden -C "iex (Get-Clipboard -Raw)"
```

That was `STARTER` in [scripts/jumpseat.py](../scripts/jumpseat.py) (`PS_JOB`). It matches a common malware pattern
(clipboard-paste or "ClickFix" attacks): launched from the Run dialog (`super+r`, which leaves a RunMRU entry), with a hidden
window, no profile, and `Invoke-Expression` running code from the clipboard.

Other things that made it worse:

- Each `ps` call starts a new hidden PowerShell, so a session with many calls raises many alerts.
- If a command waits for input (for example the NuGet provider install prompt from `Register-PSRepository`), the hidden
  process waits forever. `ps` times out, but the process is left running.

## Requirements for a fix

- No `-W Hidden`, no `iex`/`Invoke-Expression` or `[scriptblock]::Create` on clipboard content, and no `-EncodedCommand`.
  These are the patterns Defender watches for, so swapping one for another will not help.
- Do not use the Run dialog to start it.
- Give the user a visible window, so they can see what runs.
- Never wait for input: `-NonInteractive`, and a time limit that stops the process.

## Options

1. **A visible console, started once per session.** Open a normal PowerShell window through the Start menu,
   then paste the command into it as typed input and read the output from the screen or clipboard
   (`... | Set-Clipboard`). It looks like what a person does, it reuses one process, and the user sees everything.
   The command is visible in the console history and transcript.
2. **A script file.** Write the command to a `.ps1` file in `%TEMP%` (through the clipboard and `Set-Content`, typed in
   the visible console), then run it with `powershell -NoProfile -NonInteractive -File`. Script files on disk can be blocked
   or scanned by AMSI, but that is expected and not alarming.
3. **No PowerShell automation.** Drop `ps` on servers that have Defender for Endpoint, and fall back to reading the screen.
   Slow, but safe.

Whatever we pick: make it opt-in or warn about it in `SKILL.md`, tell the SOC before using it on servers, and test it on
a lab server with Defender before using it in production.

## Implemented replacement

The clipboard launcher has been removed. The opt-in [file runner](./powershell.md) uses plain `.ps1` files in a dedicated
RDP-shared folder, a visible console manually enabled by the user, and persistent output logs.
It rejects stale/session-mismatched requests and terminates timed-out commands.
GUI/copy/OCR is now the default skill guidance. Windows/RDP/Defender lab validation and SOC approval remain outstanding;
local tests cannot establish that the replacement will not alert.
