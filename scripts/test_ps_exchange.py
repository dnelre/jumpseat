import contextlib
import io
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import ps_exchange


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runner = {"session": "test-session", "computer": "LAB01", "user": "LAB\\tester",
                       "heartbeat": time.time()}
        ps_exchange.publish(self.root / "runner.json", self.runner)

    def submit(self, command="Get-Date", server="lab01", timeout=1):
        with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
            code = ps_exchange.submit(self.root, command, server, timeout)
        return code, output.getvalue()

    def test_requires_matching_live_runner_before_queuing(self):
        with self.assertRaisesRegex(ValueError, "not OTHER"):
            self.submit(server="OTHER")
        self.runner["heartbeat"] -= 20
        ps_exchange.publish(self.root / "runner.json", self.runner)
        with self.assertRaisesRegex(ValueError, "No live runner"):
            self.submit()
        self.assertFalse((self.root / "jobs").exists())

    def test_invalid_input_never_queues(self):
        for command, timeout in [("", 1), ("Get-Date", 0), ("Get-Date", 3601)]:
            with self.subTest(command=command, timeout=timeout), self.assertRaises(ValueError):
                self.submit(command=command, timeout=timeout)
        self.assertFalse((self.root / "jobs").exists())

    def test_preserves_script_text_and_returns_output_and_error_status(self):
        command = "'bl\u00e5b\u00e6r'\n@'\n'quoted'\n'@"
        failures = []

        def finish():
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    requests = list(self.root.glob("jobs/*/ready.json"))
                    if requests:
                        job = requests[0].parent
                        request = ps_exchange.read_json(requests[0])
                        self.assertEqual(request["session"], self.runner["session"])
                        self.assertEqual(request["timeout"], 1)
                        self.assertEqual((job / "command.ps1").read_text(encoding="utf-8-sig"), command + "\n")
                        (job / "output.log").write_text("partial output\nERROR: failed\n", encoding="utf-8-sig")
                        ps_exchange.publish(job / "result.json", {"exit_code": 7, "error": "failed"})
                        return
                    time.sleep(0.01)
                self.fail("No request was published")
            except Exception as error:
                failures.append(error)

        thread = threading.Thread(target=finish)
        thread.start()
        try:
            code, output = self.submit(command)
        finally:
            thread.join()
        if failures:
            raise failures[0]
        self.assertEqual(code, 7)
        self.assertEqual(output, "partial output\nERROR: failed\n")
        self.assertFalse(list(self.root.glob("jobs/*/cancel")))

    def test_client_timeout_requests_cancellation(self):
        with patch.object(ps_exchange.time, "monotonic", side_effect=[0, 100]):
            with self.assertRaisesRegex(ValueError, "Cancellation requested"):
                self.submit()
        self.assertEqual(len(list(self.root.glob("jobs/*/cancel"))), 1)

    def test_parallel_request_is_rejected(self):
        import fcntl

        with (self.root / ".client.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, "still in progress"):
                self.submit()

    def test_partial_json_is_not_a_completed_result(self):
        (self.root / "result.json").write_text('{"exit_code":')
        self.assertIsNone(ps_exchange.read_json(self.root / "result.json"))
        self.assertIsNone(ps_exchange.read_json(self.root / "missing.json"))

    def test_setup_refuses_to_replace_files_of_a_live_runner(self):
        script = self.root / "Start-Jumpseat.ps1"
        script.write_text("original")
        job = '{"op":"setup","files":{"Start-Jumpseat.ps1":"replacement"}}'
        with patch.object(ps_exchange, "ROOT", self.root), patch.dict(os.environ, JUMPSEAT_JOB=job):
            with self.assertRaisesRegex(SystemExit, "Stop the visible runner"):
                ps_exchange.main()
        self.assertEqual(script.read_text(), "original")


if __name__ == "__main__":
    unittest.main()
