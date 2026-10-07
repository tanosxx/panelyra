from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from usbdisplay import devices, network


class DeviceDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.net = self.base / "class/net"
        self.usb = self.base / "bus/usb/devices"
        self.net.mkdir(parents=True)
        self.usb.mkdir(parents=True)

    def usb_device(self, address="1-1", manufacturer="Lenovo", product="TB-X606X", parent=None):
        physical = (parent or self.base / "devices") / address
        physical.mkdir(parents=True)
        for field, value in (("idVendor", "17ef"), ("idProduct", "1234"),
                             ("manufacturer", manufacturer), ("product", product)):
            (physical / field).write_text(value)
        (self.usb / address).symlink_to(physical)
        return physical

    def usb_interface(self, physical, number=0, descriptor=("ff", "42", "01")):
        interface = physical / f"{physical.name}:1.{number}"
        interface.mkdir()
        for field, value in zip(("bInterfaceClass", "bInterfaceSubClass", "bInterfaceProtocol"),
                                descriptor):
            (interface / field).write_text(value)
        (self.usb / interface.name).symlink_to(interface)
        return interface

    def net_interface(self, physical, name="enx-tablet", number=0, driver="rndis_host"):
        interface = self.usb_interface(physical, number, ("e0", "01", "03"))
        driver_path = self.base / "drivers" / driver
        driver_path.mkdir(parents=True, exist_ok=True)
        (interface / "driver").symlink_to(driver_path)
        net_entry = self.net / name
        net_entry.mkdir()
        (net_entry / "device").symlink_to(interface)

    def discover(self, links=()):
        return devices.discover_devices(sys_net_root=self.net, sys_usb_root=self.usb, links=links)

    def test_tether_name_and_known_address(self):
        tablet = self.usb_device()
        self.net_interface(tablet)
        link = network.UsbLink("enx-tablet", "192.168.42.50", 24, "192.168.42.129")
        self.assertEqual(self.discover([link]), [devices.Device(
            "Lenovo TB-X606X", "usb", "usb-ready", "enx-tablet", "192.168.42.129")])

    def test_rndis_without_dhcp_or_peer_is_visible(self):
        self.net_interface(self.usb_device())
        for links in ([], [network.UsbLink("enx-tablet", "192.168.42.50", 24, None)]):
            with self.subTest(links=links):
                result = self.discover(links)
                self.assertEqual(result[0].state, "usb-no-address")
                self.assertIsNone(result[0].tablet_ip)

    def test_mtp_android_and_adb_are_visible_without_tether(self):
        self.usb_interface(self.usb_device(), descriptor=("06", "01", "01"))
        self.usb_interface(self.usb_device("2-2", "Example", "Android"),
                           descriptor=("06", "01", "01"))
        self.usb_interface(self.usb_device("3-3", "Other", "Model 123"))
        result = self.discover()
        self.assertEqual(len(result), 3)
        self.assertTrue(all(device.state == "usb-disabled" for device in result))
        self.assertEqual({device.name for device in result},
                         {"Lenovo TB-X606X", "Example Android", "Other Model 123"})

    def test_excludes_camera_keyboard_hub_and_usb_ethernet(self):
        for address, product, descriptor in (
                ("1-1", "Webcam", ("0e", "01", "00")),
                ("1-2", "Camera", ("06", "01", "01")),
                ("1-3", "Keyboard", ("03", "01", "01")),
                ("1-4", "USB Hub", ("09", "00", "00")),
                ("1-5", "Android accessory", ("03", "00", "00"))):
            self.usb_interface(self.usb_device(address, "Lenovo", product), descriptor=descriptor)
        self.net_interface(self.usb_device("1-6", "Example", "Ethernet adapter"), driver="cdc_ether")
        self.assertEqual(self.discover(), [])

    def test_tether_and_adb_deduplicated_per_physical_device(self):
        tablet = self.usb_device()
        self.net_interface(tablet, name="enx-first")
        self.net_interface(tablet, name="enx-second", number=1)
        self.usb_interface(tablet, number=2)
        links = [network.UsbLink("enx-second", "192.168.42.50", 24, None),
                 network.UsbLink("enx-second", "192.168.42.51", 24, "192.168.42.129")]
        result = self.discover(links)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].interface, "enx-second")
        self.assertEqual(result[0].state, "usb-ready")

    def test_multiple_tablets_are_returned_with_ready_first(self):
        self.usb_interface(self.usb_device("1-1", "A", "Android"))
        self.net_interface(self.usb_device("2-2", "Z", "Tablet"))
        link = network.UsbLink("enx-tablet", "192.168.42.50", 24, "192.168.42.129")
        result = self.discover([link])
        self.assertEqual([device.state for device in result], ["usb-ready", "usb-disabled"])
        self.assertEqual({device.name for device in result}, {"A Android", "Z Tablet"})

    def test_missing_descriptors_use_generic_name_without_serial(self):
        tablet = self.usb_device(manufacturer="", product="")
        (tablet / "serial").write_text("PRIVATE-SERIAL")
        self.net_interface(tablet)
        self.assertEqual(self.discover()[0].name, "USB")

    def test_manufacturer_is_not_repeated_and_description_is_single_line(self):
        self.usb_interface(self.usb_device(product="Lenovo Tab\n M10\x00"))
        self.assertEqual(self.discover()[0].name, "Lenovo Tab M10")

    def test_no_devices_and_disconnected_symlinks(self):
        (self.usb / "gone").symlink_to(self.base / "missing")
        (self.net / "gone").symlink_to(self.base / "also-missing")
        self.assertEqual(self.discover(), [])

    def test_disconnect_after_driver_read_does_not_identify_upstream_hub(self):
        hub = self.usb_device(manufacturer="Example", product="USB Hub")
        (hub / "bDeviceClass").write_text("09")
        tablet = self.usb_device("1-1.1", parent=hub)
        self.net_interface(tablet)
        find_parent = devices._usb_parent

        def unplug_before_reading_parent(path):
            # discover_devices has already resolved the RNDIS driver. Keep
            # the old net symlink and upstream hub, as in a USB removal race.
            if tablet.exists():
                shutil.rmtree(tablet)
            return find_parent(path)

        link = network.UsbLink("enx-tablet", "192.168.42.50", 24, "192.168.42.129")
        self.assertEqual(self.discover([link]), [devices.Device(
            "Lenovo TB-X606X", "usb", "usb-ready", "enx-tablet", "192.168.42.129")])
        with patch.object(devices, "_usb_parent", side_effect=unplug_before_reading_parent):
            self.assertEqual(self.discover([link]), [])

    def test_network_discovery_used_only_when_links_not_supplied(self):
        with patch.object(network, "discover_links", return_value=[]) as discover:
            self.discover()
            discover.assert_not_called()
            devices.discover_devices(sys_net_root=self.net, sys_usb_root=self.usb)
            discover.assert_called_once_with()

    def test_network_discovery_failure_does_not_look_like_no_devices(self):
        with patch.object(network, "discover_links", side_effect=RuntimeError("ip unavailable")):
            with self.assertRaisesRegex(RuntimeError, "ip unavailable"):
                devices.discover_devices(sys_net_root=self.net, sys_usb_root=self.usb)


if __name__ == "__main__":
    unittest.main()
