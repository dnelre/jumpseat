"""File protocol for the explicitly enabled Windows PowerShell runner."""

import fcntl
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path("/home/ubuntu/Desktop/Jumpseat")


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def publish(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def submit(root, command, server, timeout):
    if not command.strip():
        raise ValueError("Give a non-empty PowerShell command.")
    if not 1 <= timeout <= 3600:
        raise ValueError("--wait must be between 1 and 3600 seconds.")
    if not root.is_dir():
        raise ValueError("Run ps-setup, share the folder over RDP and manually start the visible runner first.")
    with (root / ".client.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another PowerShell request is still in progress.") from None
        runner = read_json(root / "runner.json")
        if not runner or not 0 <= time.time() - runner["heartbeat"] < 10:
            raise ValueError("No live runner. Manually start Start-Jumpseat.ps1 in a visible Windows console.")
        if runner["computer"].casefold() != server.casefold():
            raise ValueError("Runner is on %s, not %s. Nothing was queued." % (runner["computer"], server))
        job = root / "jobs" / uuid.uuid4().hex
        job.mkdir(parents=True)
        # Windows PowerShell 5.1 needs a BOM to recognise non-ASCII script text.
        (job / "command.ps1").write_text(command + "\n", encoding="utf-8-sig")
        print("[server: %s; user: %s; logs: %s]" % (runner["computer"], runner["user"], job),
              file=sys.stderr, flush=True)
        completed = False
        try:
            publish(job / "ready.json", {
                "session": runner["session"], "timeout": timeout, "expires": time.time() + 10,
            })
            deadline = time.monotonic() + timeout + 15
            while time.monotonic() < deadline:
                result = read_json(job / "result.json")
                if result is not None:
                    completed = True
                    log = job / "output.log"
                    if log.exists():
                        sys.stdout.write(log.read_text(encoding="utf-8-sig"))
                    if result["error"]:
                        print(result["error"], file=sys.stderr)
                    return result["exit_code"]
                time.sleep(0.2)
            raise ValueError("Runner did not return a result. Cancellation requested; check the visible console and %s."
                             % job)
        finally:
            if not completed:
                (job / "cancel").touch()


def main():
    job = json.loads(os.environ["JUMPSEAT_JOB"])
    try:
        if job["op"] == "setup":
            ROOT.mkdir(parents=True, exist_ok=True)
            os.chmod(ROOT, 0o700)
            runner = read_json(ROOT / "runner.json")
            if runner and 0 <= time.time() - runner["heartbeat"] < 10:
                raise ValueError("Stop the visible runner before updating its script files.")
            for name, content in job["files"].items():
                (ROOT / name).write_text(content, encoding="utf-8-sig")
            print("Folder: %s\nShare only this folder in Remmina, then start Start-Jumpseat.ps1 manually "
                  "from a visible Windows PowerShell console. See docs/powershell.md." % ROOT)
            return 0
        return submit(ROOT, job["command"], job["server"], job["wait"])
    except (OSError, ValueError, KeyError) as error:
        sys.exit("PowerShell file exchange failed: %s" % error)


if __name__ == "__main__":
    sys.exit(main())
