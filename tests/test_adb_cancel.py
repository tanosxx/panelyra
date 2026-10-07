"""Interrupt ADB forwarding allocation without a device or an ADB server."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


FAKE_ADB = r'''
import json, os, signal, sys, time
from pathlib import Path
args = sys.argv[1:]
if args[:1] == ['-s']:
    args = args[2:]
if args == ['-d', 'get-serialno']:
    print('tablet-test')
elif args == ['get-state']:
    print('device')
elif args[:2] == ['forward', 'tcp:0']:
    state = Path(os.environ['TEST_ADB_STATE'])
    ports = json.loads(state.read_text())
    ports.append(40123)
    state.write_text(json.dumps(ports))
    blocked = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    Path(os.environ['TEST_ADB_ALLOCATED']).write_text(json.dumps({
        'sigint_blocked': signal.SIGINT in blocked,
        'sigterm_blocked': signal.SIGTERM in blocked,
    }))
    # Simulate an already-created server-side rule whose port is not returned yet.
    time.sleep(float(os.environ.get('TEST_ADB_DELAY', '.6')))
    print('40123', flush=True)
elif args == ['forward', '--remove', 'tcp:40123']:
    state = Path(os.environ['TEST_ADB_STATE'])
    ports = json.loads(state.read_text())
    ports.remove(40123)
    state.write_text(json.dumps(ports))
    with open(os.environ['TEST_ADB_REMOVED'], 'a') as log:
        log.write('40123\n')
else:
    raise SystemExit('Unexpected fake ADB arguments: ' + repr(args))
'''

RUNNER = r'''
import os, signal
from usbdisplay.adb import Tunnel

def cancel(*_):
    raise KeyboardInterrupt

if os.environ.get('TEST_DEFAULT_SIGTERM') != '1':
    signal.signal(signal.SIGTERM, cancel)
try:
    tunnel = Tunnel()
    with tunnel:
        print('entered', flush=True)
    tunnel.__exit__(None, None, None)  # cleanup must be idempotent
except KeyboardInterrupt:
    print('cancelled', flush=True)
'''


class AdbCancellationTests(unittest.TestCase):
    def run_allocation(self, signum=None, group=False, default_sigterm=False):
        with tempfile.TemporaryDirectory(prefix='panelyra-adb-test-') as temporary:
            folder = Path(temporary)
            fake = folder / 'adb'
            fake.write_text(f'#!{sys.executable}\n' + FAKE_ADB)
            fake.chmod(0o700)
            state = folder / 'ports.json'
            state.write_text('[7777]')  # unrelated rule must survive every path
            allocated = folder / 'allocated'
            removed = folder / 'removed'
            environment = {**os.environ,
                           'PATH': str(folder) + os.pathsep + os.environ.get('PATH', ''),
                           'TEST_ADB_STATE': str(state), 'TEST_ADB_ALLOCATED': str(allocated),
                           'TEST_ADB_REMOVED': str(removed),
                           'TEST_ADB_DELAY': '.6' if signum is not None else '0',
                           'TEST_DEFAULT_SIGTERM': '1' if default_sigterm else '0'}
            process = subprocess.Popen([sys.executable, '-c', RUNNER],
                                       cwd=Path(__file__).resolve().parents[1], env=environment,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       start_new_session=True)
            try:
                deadline = time.monotonic() + 5
                while not allocated.is_file():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        self.fail('Fake ADB did not allocate the forwarding rule')
                    time.sleep(.01)
                if signum is not None:
                    if group:
                        os.killpg(process.pid, signum)
                    else:
                        process.send_signal(signum)
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, -signal.SIGTERM if default_sigterm else 0, stderr)
                self.assertEqual(json.loads(state.read_text()), [7777])
                self.assertEqual(removed.read_text().splitlines(), ['40123'])
                self.assertEqual(json.loads(allocated.read_text()),
                                 {'sigint_blocked': True, 'sigterm_blocked': True})
                if signum is None:
                    self.assertIn('entered', stdout)
                elif not default_sigterm:
                    self.assertIn('cancelled', stdout)
                    self.assertNotIn('entered', stdout)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate(timeout=5)

    def test_gui_sigterm_during_allocation_removes_only_own_rule(self):
        self.run_allocation(signal.SIGTERM)

    def test_terminal_sigint_is_deferred_in_parent_and_adb_child(self):
        self.run_allocation(signal.SIGINT, group=True)

    def test_default_sigterm_also_cleans_before_delivery(self):
        self.run_allocation(signal.SIGTERM, default_sigterm=True)

    def test_successful_context_removes_rule_once(self):
        self.run_allocation()


if __name__ == '__main__':
    unittest.main()
