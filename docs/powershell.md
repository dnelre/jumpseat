# Opt-in PowerShell file runner

The old hidden clipboard launcher triggered Defender. It has been removed. The replacement uses plain script files,
a manually started visible console, and output logs. It does not change Defender, execution policy or other security controls.
**No guarantee of zero alerts:** get SOC approval and validate on a Defender-protected lab server before production use.

## Set up once

1. Run `python3 scripts/jumpseat.py ps-setup`. This creates `Jumpseat` on the Linux/noVNC Desktop.
2. In your saved Remmina RDP profile, set **Share folder** to `/home/ubuntu/Desktop/Jumpseat`.
   Share only this folder, not your personal Desktop or home folder. Reconnecting may be necessary:
   ask before disconnecting an active session. If drive redirection is blocked, do not bypass the policy;
   use screen reading instead.
3. In Windows Explorer, find the redirected folder under **This PC** and copy its UNC address.
   The share name depends on the client; do not assume it is `Jumpseat`.
4. Open an ordinary **Windows PowerShell** console through the Start menu, not the Run dialog.
   Run the following, replacing the placeholder with that folder's actual address:

   ```powershell
   & '\\tsclient\<share-name>\Start-Jumpseat.ps1'
   ```

5. Review the displayed server and account. Type `START` yourself to enable execution.
   Keep the console visible; Ctrl+C stops it and attempts to stop the active command tree.
   If organizational policy requires signed scripts, every generated `command.ps1` must satisfy it too.
   No signing integration is provided: if blocked, use the GUI or an approved signing workflow, not a policy bypass.

Only one runner may use the folder at a time. Stop it before running `ps-setup` again.
Anyone able to write to the shared folder can submit commands with the Windows account's permissions.
Enable it only while needed. Ordinary GUI operations do not require this runner.

## Run a query

```sh
python3 scripts/jumpseat.py ps --server LAB01 'Get-Date'
python3 scripts/jumpseat.py ps --server LAB01 --wait 60 'Get-Service | Select-Object Name,Status'
```

Use the exact computer name displayed by the runner, not a DNS alias. The command refuses to queue unless a recent
heartbeat matches that server. It is not tied to the focused Remmina tab: check the printed server and account.
Use one dedicated shared folder/session at a time.

Each request gets a folder under `Jumpseat/jobs` containing:

- `command.ps1`: the submitted command, written as UTF-8 with a BOM for Windows PowerShell 5.1.
- `running.json` (initially `ready.json`): runner session, expiry and timeout.
- `output.log`: text output and reported errors, also displayed in the Windows console.
- `result.json`: exit code and runner errors.

You can open these in the Linux Desktop folder or through the redirected folder in Windows. The agent reads them through
Docker, not the clipboard or another connection to the server. Nothing automatically opens a log viewer.
An RDP disconnect may interrupt drive access: stop and restart the runner after reconnecting if necessary.

Commands run in a fresh noninteractive PowerShell process. Variables and working-directory changes are not preserved.
Errors return a nonzero exit code; partial output remains in the log. `--wait` limits execution to 1–3600 seconds
(default 30). On timeout/cancellation the runner uses Windows `taskkill /PID ... /T /F` to stop the command tree.
The client allows a short additional interval for startup, stopping and log publication.
If the file-exchange helper times out, it leaves a cancellation file and reports an error.
A lost Docker/RDP connection cannot guarantee cancellation; verify the process stopped in the console.
Expired requests and requests from previous runner sessions are rejected rather than replayed.

This is not a sandbox or rollback mechanism. Termination cannot undo changes, stop remote work already submitted,
or guarantee cleanup of detached processes. Use it for bounded queries; still ask before state-changing commands.
Logs and scripts may contain sensitive data. They persist in the desktop home volume: review and delete completed job folders
when no longer needed, with the runner stopped. Nothing is automatically uploaded.

## Validation

```sh
python3 -m unittest discover -s scripts -p 'test_*.py' -v
docker run --rm -v "$PWD:/workspace:ro" mcr.microsoft.com/powershell:7.4-ubuntu-22.04 \
  pwsh -NoLogo -NoProfile -File /workspace/scripts/test_powershell.ps1
```

The isolated tests cover script execution, Unicode logs, command errors, input rejection, exit codes, expiry,
session mismatch and timeout recovery. Linux tests substitute process termination for Windows `taskkill`;
they do not validate Windows process-tree termination, RDP drive locking or Defender behavior.
Run `scripts/test_powershell.ps1` with Windows PowerShell 5.1 on an approved lab machine too, and check SOC telemetry
while testing the actual redirected folder. Do not treat passing local tests as production approval.
