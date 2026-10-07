import unittest
from unittest.mock import patch

from usbdisplay.protocol import FRAME, MAX_FRAME, frame_header, hello, validate_mode
from usbdisplay.adb import Tunnel, usb_devices


class ProtocolTests(unittest.TestCase):
    def test_wire_format_matches_android_data_input_stream(self):
        self.assertEqual(hello(1280, 800, 20),
                         b"UTD1\x00\x00\x05\x00\x00\x00\x03\x20\x00\x00\x00\x14")
        self.assertEqual(FRAME.unpack(frame_header(4096, 50_000)), (4096, 50_000))

    def test_rejects_unsafe_modes_and_lengths(self):
        for mode in ((0, 800, 20), (1281, 800, 20), (4096, 4096, 20), (1280, 800, 0)):
            with self.assertRaises(ValueError):
                validate_mode(*mode)
        for size in (0, -1, MAX_FRAME + 1):
            with self.assertRaises(ValueError):
                frame_header(size, 0)
        for pts in (-1, 2**63):
            with self.assertRaises(ValueError):
                frame_header(1, pts)

    def test_device_listing_does_not_require_usb_descriptor(self):
        output = ("List of devices attached\n"
                  "tablet device usb:1-2 product:x model:Lenovo transport_id:1\n"
                  "locked unauthorized usb:1-3 transport_id:2\n"
                  "libusb device product:x transport_id:5\n"
                  "192.168.1.9:5555 device product:x transport_id:3\n"
                  "emulator-5554 device product:sdk transport_id:4\n")
        with patch("usbdisplay.adb.run_adb", return_value=output):
            self.assertIn(("libusb", "device"), usb_devices())

    def test_failure_still_removes_only_owned_forward(self):
        with patch("usbdisplay.adb.usb_devices", return_value=[("tablet", "device")]), \
             patch("usbdisplay.adb.run_adb", return_value="41234") as adb:
            with self.assertRaises(RuntimeError):
                with Tunnel("tablet"):
                    raise RuntimeError("Disconnected")
            self.assertEqual(adb.call_args.args, ("forward", "--remove", "tcp:41234"))
            self.assertEqual(adb.call_args.kwargs, {"serial": "tablet"})

    def test_ambiguous_device_never_creates_tunnel(self):
        with patch("usbdisplay.adb.run_adb", side_effect=RuntimeError("multiple devices")) as adb:
            with self.assertRaises(RuntimeError):
                with Tunnel():
                    pass
            adb.assert_called_once_with("-d", "get-serialno")

    def test_default_uses_adb_usb_transport_filter(self):
        with patch("usbdisplay.adb.run_adb", side_effect=["libusb", "device", "41234", ""]) as adb:
            with Tunnel() as tunnel:
                self.assertEqual(tunnel.serial, "libusb")
            self.assertEqual(adb.call_args_list[0].args, ("-d", "get-serialno"))


if __name__ == "__main__":
    unittest.main()
