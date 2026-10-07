"""Own one ADB forwarding rule; never alter other applications' rules."""

import shutil
import signal
import socket
import subprocess
import time


def run_adb(*args, serial=None, timeout=15):
    if not shutil.which("adb"):
        raise RuntimeError("ADB не установлен. Установите пакет adb (см. README.md)")
    command = ["adb"] + (["-s", serial] if serial else []) + list(args)
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"ADB не ответил за {timeout} секунд") from error
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Ошибка ADB")
    return result.stdout.strip()


def usb_devices():
    # Informational list only: libusb can omit the usb: descriptor.
    lines = run_adb("devices", "-l").splitlines()
    result = []
    for line in lines:
        fields = line.split()
        if len(fields) >= 2 and fields[1] in ("device", "unauthorized", "offline", "recovery", "sideload"):
            result.append((fields[0], fields[1]))
    return result


class Tunnel:
    def __init__(self, serial=None, device_port=27183):
        self.serial = serial
        self.device_port = device_port
        self.port = None

    def __enter__(self):
        if self.serial:
            # An explicit serial is chosen by the user. Exclude conventional
            # network/emulator IDs; absence of usb: is NOT evidence of Wi-Fi.
            if ":" in self.serial or self.serial.startswith("emulator-") or "._tcp" in self.serial:
                raise RuntimeError("Укажите серийный номер USB-устройства, а не сетевой адрес/эмулятор")
            devices = usb_devices()
            devices = [device for device in devices if device[0] == self.serial]
            if len(devices) != 1:
                raise RuntimeError("Устройство не найдено. Проверьте --serial через adb devices -l")
            self.serial, state = devices[0]
        else:
            try:
                self.serial = run_adb("-d", "get-serialno")
                state = run_adb("get-state", serial=self.serial)
            except RuntimeError as error:
                raise RuntimeError("ADB не смог выбрать USB-планшет. Подключите кабель, подтвердите "
                                   "отладку; для нескольких устройств укажите --serial.\n" + str(error)) from error
        if state != "device":
            raise RuntimeError(f"Планшет: {state}. Разблокируйте его и подтвердите отладку по USB")
        # ADB creates the rule in its server before printing the allocated port.
        # Defer cancellation until we know that port, including in the subprocess:
        # a terminal SIGINT reaches the whole process group, not just Python.
        deferred_signals = {signal.SIGINT, signal.SIGTERM}
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, deferred_signals)
        cancelled = False
        try:
            try:
                port = run_adb("forward", "tcp:0", f"tcp:{self.device_port}", serial=self.serial)
                self.port = int(port)
            finally:
                try:
                    cancelled = bool(signal.sigpending() & (deferred_signals - previous_mask))
                    if cancelled:
                        # Clean up before delivery, even if the saved SIGTERM
                        # disposition exits the process instead of raising.
                        self._remove_forward()
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            if cancelled:
                # A custom handler may return normally; cancellation still must
                # not hand a removed forwarding rule to the caller.
                raise KeyboardInterrupt
        except BaseException:
            # __exit__ is not called when __enter__ itself was interrupted.
            self._remove_forward()
            raise
        return self

    def connect(self, launch=True):
        if launch:
            output = run_adb("shell", "am", "start", "-n",
                             "dev.usbdisplay.client/.MainActivity", serial=self.serial)
            if "Error" in output or "Exception" in output:
                raise RuntimeError("Не удалось открыть Panelyra. Установите APK на планшет.\n" + output)
        deadline = time.monotonic() + 8
        while True:
            connection = None
            try:
                connection = socket.create_connection(("127.0.0.1", self.port), timeout=2)
                # ADB may accept locally before the device listener is ready.
                # Closed forwards become readable EOF immediately.
                import select
                readable, _, _ = select.select([connection], [], [], 0.15)
                if readable and not connection.recv(1, socket.MSG_PEEK):
                    raise ConnectionError("Приёмник ещё не готов")
                connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
                connection.settimeout(3)
                return connection
            except OSError as error:
                if connection is not None:
                    connection.close()
                if time.monotonic() >= deadline:
                    raise RuntimeError("Откройте Panelyra на планшете и повторите запуск") from error
                time.sleep(0.2)

    def __exit__(self, *_):
        self._remove_forward()

    def _remove_forward(self):
        if self.port is not None:
            port, self.port = self.port, None
            try:
                run_adb("forward", "--remove", f"tcp:{port}", serial=self.serial)
            except RuntimeError as error:
                print(f"Не удалось удалить правило ADB tcp:{port}: {error}")
