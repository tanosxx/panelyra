"""Exercise stream ownership with isolated real child processes, never a tablet."""

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from usbdisplay import runtime
from usbdisplay.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
HOLDER = r'''
import signal
import sys
from pathlib import Path
from usbdisplay import runtime

def stopped(signum, frame):
    Path(sys.argv[1]).write_text(str(signum))
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stopped)
with runtime.Instance({"width": 1600, "height": 1000, "fps": 30}):
    print("READY", flush=True)
    sys.stdin.readline()
'''


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux flock and pidfd")
class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="panelyra-runtime-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.env = dict(os.environ, XDG_RUNTIME_DIR=str(self.base),
                        PYTHONPATH=str(ROOT), DISPLAY="", WAYLAND_DISPLAY="",
                        DBUS_SESSION_BUS_ADDRESS="")
        self.environment = patch.dict(os.environ, self.env)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "usbdisplay", *args],
                              cwd=self.base, env=self.env, capture_output=True,
                              text=True, timeout=10)

    def holder(self):
        marker = self.base / "terminated"
        process = subprocess.Popen([sys.executable, "-u", "-c", HOLDER, str(marker)],
                                   cwd=self.base, env=self.env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def cleanup():
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=3)
        self.addCleanup(cleanup)
        ready, _, _ = select.select([process.stdout], [], [], 5)
        self.assertTrue(ready, "Test lock holder did not become ready")
        line = process.stdout.readline()
        self.assertEqual(line, "READY\n", process.stderr.read() if process.poll() is not None else line)
        return process, marker

    def locked_record(self, record):
        # Editing the contents preserves the inode and the child's advisory lock.
        runtime.runtime_path().write_text(json.dumps(record), encoding="utf-8")

    def test_second_cli_start_is_rejected_while_first_process_holds_stream(self):
        process, _ = self.holder()
        status = self.cli("status", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        record = json.loads(status.stdout)
        self.assertTrue(record["running"])
        self.assertEqual(record["pid"], process.pid)
        self.assertEqual(record["width"], 1600)
        second = self.cli("start", "--test-pattern")
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("already running", second.stderr)
        self.assertIsNone(process.poll())
        self.assertEqual(runtime.status()["pid"], process.pid)

    def test_cli_stop_sends_sigterm_to_owned_child_and_releases_lock(self):
        process, marker = self.holder()
        stopped = self.cli("stop")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertEqual(marker.read_text(), str(signal.SIGTERM))
        self.assertEqual(runtime.status(), {"running": False})
        self.assertEqual(runtime.runtime_path().read_bytes(), b"")
        self.assertEqual(self.cli("stop").returncode, 0)
        # The same lock inode remains usable after shutdown.
        with runtime.Instance({"fps": 20}):
            self.assertEqual(runtime.status()["pid"], os.getpid())

    def test_unlocked_stale_record_never_stops_a_live_process(self):
        process, _ = self.holder()
        alternate = self.base / "stale.lock"
        alternate.write_text(json.dumps({"pid": process.pid,
                                         "start": runtime.process_start(process.pid)}))
        alternate.chmod(0o600)
        self.assertEqual(runtime.status(alternate), {"running": False})
        self.assertFalse(runtime.stop(alternate))
        self.assertIsNone(process.poll())

    def test_reused_pid_identity_is_rejected_even_while_lock_is_held(self):
        process, marker = self.holder()
        actual = runtime.process_start(process.pid)
        self.assertIsNotNone(actual)
        self.locked_record({"pid": process.pid, "start": str(int(actual) + 1)})
        self.assertEqual(runtime.status(), {"running": False})
        self.assertFalse(runtime.stop())
        self.assertIsNone(process.poll())
        self.assertFalse(marker.exists())

    def test_null_identity_and_nonexistent_pid_cannot_be_reported_as_running(self):
        process, marker = self.holder()
        # This also verifies that a huge malformed PID never reaches pidfd_open.
        for record in ({"pid": 2**100, "start": None}, {"pid": 2**100}):
            with self.subTest(record=record):
                self.locked_record(record)
                self.assertEqual(runtime.status(), {"running": False})
                self.assertFalse(runtime.stop())
        self.assertIsNone(process.poll())
        self.assertFalse(marker.exists())

    def test_partial_or_malformed_locked_state_never_signals_holder(self):
        process, marker = self.holder()
        for contents in ("", "{", "[]", '"oops"', "null",
                         '{"pid": true, "start": "1"}', '{"pid": "123", "start": "1"}'):
            with self.subTest(contents=contents):
                runtime.runtime_path().write_text(contents)
                state = runtime.status()
                self.assertTrue(not state.get("running") or state.get("starting"))
                try:
                    self.assertFalse(runtime.stop())
                except RuntimeError as error:
                    self.assertIn("starting", str(error))
                self.assertIsNone(process.poll())
                self.assertFalse(marker.exists())

    def test_symlink_state_file_is_rejected_without_modifying_target(self):
        victim = self.base / "unrelated"
        victim.write_text("keep me")
        link = self.base / "symlink.lock"
        link.symlink_to(victim)
        for operation in (runtime.status, runtime.stop):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises((OSError, RuntimeError)):
                    operation(link)
        with self.assertRaises((OSError, RuntimeError)):
            with runtime.Instance(path=link):
                self.fail("A symlink must not be locked")
        self.assertEqual(victim.read_text(), "keep me")

    def test_shared_state_permissions_and_symlink_directory_are_rejected(self):
        state = self.base / "shared.lock"
        state.write_text("keep me")
        state.chmod(0o644)
        with self.assertRaises(RuntimeError):
            runtime.status(state)
        self.assertEqual(state.read_text(), "keep me")
        target = self.base / "elsewhere"
        target.mkdir(mode=0o700)
        (self.base / "panelyra").symlink_to(target, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            runtime.runtime_path()
        self.assertEqual(list(target.iterdir()), [])

    def test_cli_status_and_profiles_need_no_display_or_connected_device(self):
        status = self.cli("status", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout), {"running": False})
        profiles = self.cli("profiles", "--json")
        self.assertEqual(profiles.returncode, 0, profiles.stderr)
        modes = json.loads(profiles.stdout)
        self.assertIn("balanced", modes)
        self.assertGreaterEqual(len(modes), 2)
        for name, mode in modes.items():
            with self.subTest(profile=name):
                Settings(**mode).validate()
        self.assertEqual(self.cli("stop").returncode, 0)


if __name__ == "__main__":
    unittest.main()
