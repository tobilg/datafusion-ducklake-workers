"""Regressions for fixture updates and process ownership, without cloud access."""

import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devlib import service, update_secrets

CALLER = "API_" + "KEY"
ACCESS = "S3_ACCESS_" + "KEY_ID"


class WorkflowTests(unittest.TestCase):
    def test_secret_update_preserves_caller_and_restricts_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".dev.vars"
            path.write_text("API_KEY=existing\nS3_ACCESS_KEY_ID=old\n")
            path.chmod(0o644)
            update_secrets(path, {ACCESS: "new"})
            self.assertEqual(
                path.read_text(), "API_KEY=existing\nS3_ACCESS_KEY_ID=new\n"
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            update_secrets(path, {ACCESS: "new"})
            self.assertEqual(path.read_text().count("API_KEY="), 1)

    def test_multiline_secret_does_not_truncate_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".dev.vars"
            path.write_text("API_KEY=existing\n")
            with self.assertRaises(ValueError):
                update_secrets(path, {CALLER: "bad\nvalue"})
            self.assertEqual(path.read_text(), "API_KEY=existing\n")

    def test_busy_port_is_not_adopted_or_stopped(self):
        with patch("devlib.socket.socket") as socket_type, patch(
            "devlib.subprocess.Popen"
        ) as start:
            socket_type.return_value.__enter__.return_value.connect_ex.return_value = 0
            with self.assertRaisesRegex(RuntimeError, "occupied"):
                with service(["unused"], 8999, "unused.log"):
                    self.fail("Must not enter")
            start.assert_not_called()

    def test_failed_service_is_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".cache").mkdir()
            with patch("devlib.ROOT", root), patch(
                "devlib.socket.socket"
            ) as sockets, patch("devlib.subprocess.Popen") as start, patch(
                "devlib.os.killpg"
            ) as kill:
                sockets.return_value.__enter__.return_value.connect_ex.return_value = 1
                process = start.return_value
                process.poll.return_value = 1
                with self.assertRaisesRegex(RuntimeError, "exited"):
                    with service(["unused"], 8999, "worker.log"):
                        pass
                kill.assert_called_once()
                process.wait.assert_called_once()


if __name__ == "__main__":
    unittest.main()
