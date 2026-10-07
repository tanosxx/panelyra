import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from usbdisplay import network


class UsbNetworkTests(unittest.TestCase):
    def setUp(self):
        self.link = network.UsbLink("enx-tablet", "192.168.42.50", 24, "192.168.42.129")

    def test_only_usb_backed_tether_drivers_are_accepted(self):
        # Model sysfs links, including a USB Wi-Fi adapter and a PCI device whose
        # driver name would otherwise look eligible. Interface names alone prove nothing.
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            sys_root = base / "class/net"

            def device(name, driver, usb):
                hardware = base / "devices" / name
                hardware.mkdir(parents=True)
                if usb:
                    (hardware / "idVendor").write_text("17ef")
                interface_device = hardware / "function"
                interface_device.mkdir()
                driver_dir = base / "drivers" / driver
                driver_dir.mkdir(parents=True, exist_ok=True)
                (interface_device / "driver").symlink_to(driver_dir)
                entry = sys_root / name
                entry.mkdir(parents=True)
                (entry / "device").symlink_to(interface_device)

            device("eth0", "rndis_host", True)
            device("wlan0", "rtl8xxxu", True)
            device("enp1s0", "rndis_host", False)
            device("eth1", "e1000e", False)
            self.assertEqual(network.usb_driver("eth0", sys_root), "rndis_host")
            for name in ("wlan0", "enp1s0", "eth1", "missing"):
                with self.subTest(interface=name):
                    self.assertIsNone(network.usb_driver(name, sys_root))

    @staticmethod
    def address(name, local, *, up=True):
        return {"ifname": name, "flags": ["UP"] if up else [], "addr_info": [
            {"family": "inet", "scope": "global", "local": local, "prefixlen": 24}
        ]}

    def command_output(self, addresses, routes, options=""):
        def run(*args):
            if args == ("ip", "-j", "-4", "address", "show"):
                return json.dumps(addresses)
            if args == ("ip", "-j", "-4", "route", "show"):
                return json.dumps(routes)
            if args[:5] == ("nmcli", "-g", "DHCP4.OPTION", "device", "show"):
                return options
            raise AssertionError(f"Unexpected command: {args!r}")
        return run

    def test_discovery_excludes_wifi_non_usb_and_down_links(self):
        addresses = [self.address("wlan0", "192.168.1.12"),
                     self.address("eth0", "10.0.0.12"),
                     self.address("usb-down", "192.168.43.50", up=False),
                     self.address("enx-tablet", "192.168.42.50")]
        routes = [{"dev": "wlan0", "gateway": "192.168.1.1"},
                  {"dev": "enx-tablet", "gateway": "192.168.42.129"}]
        with patch.object(network, "usb_driver", side_effect=lambda name: (
                "rndis_host" if name in {"enx-tablet", "usb-down"} else None)), \
             patch.object(network, "command", side_effect=self.command_output(addresses, routes)):
            self.assertEqual(network.select_link(), self.link)

    def test_gateway_survives_never_default_via_dhcp_metadata(self):
        # A live never-default connection retains its connected subnet route and
        # DHCP lease, but deliberately has no default gateway in the route table.
        routes = [{"dst": "192.168.42.0/24", "dev": "enx-tablet", "scope": "link"}]
        options = ("dhcp_server_identifier = 192.168.42.1 | lease_time = 3600 | "
                   "routers = 192.168.42.129 | subnet_mask = 255.255.255.0")
        with patch.object(network, "usb_driver", return_value="rndis_host"), \
             patch.object(network, "command", side_effect=self.command_output(
                 [self.address("enx-tablet", self.link.local_ip)], routes, options)):
            self.assertEqual(network.select_link(), self.link)

    def test_generic_usb_ethernet_requires_explicit_interface(self):
        addresses = [self.address("usb-lan", "192.168.42.50")]
        routes = [{"dev": "usb-lan", "gateway": "192.168.42.129"}]
        with patch.object(network, "usb_driver", return_value="cdc_ether"), \
             patch.object(network, "command", side_effect=self.command_output(addresses, routes)):
            self.assertEqual(network.discover_links(), [])
            self.assertEqual(network.select_link(interface="usb-lan").interface, "usb-lan")

    def test_dhcp_server_is_fallback_when_no_router_was_announced(self):
        self.assertEqual(network.dhcp_gateway(
            "lease_time = 3600\ndhcp_server_identifier = 192.168.42.129\n"),
            self.link.tablet_ip)

    def test_explicit_peer_rejects_unrelated_or_non_host_addresses(self):
        for address in ("192.168.1.2", "192.168.42.50", "192.168.42.0",
                        "192.168.42.255", "127.0.0.1", "224.0.0.1", "0.0.0.0",
                        "::1", "tablet.local"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.link.validate_peer(address)
        self.assertEqual(self.link.validate_peer("192.168.42.129"), "192.168.42.129")

    def test_explicit_off_subnet_peer_cannot_select_the_wrong_usb_link(self):
        with patch.object(network, "usb_driver", return_value="rndis_host"), \
             patch.object(network, "command", side_effect=self.command_output(
                 [self.address("enx-tablet", self.link.local_ip)], [])), \
             self.assertRaises(RuntimeError):
            network.select_link(tablet_ip="192.168.1.2")

    def test_explicit_peer_works_without_nmcli_or_default_route(self):
        with patch.object(network, "usb_driver", return_value="rndis_host"), \
             patch.object(network, "command", side_effect=self.command_output(
                 [self.address("enx-tablet", self.link.local_ip)], [])) as commands:
            self.assertEqual(network.select_link(tablet_ip=self.link.tablet_ip), self.link)
            self.assertFalse(any(c.args[0] == "nmcli" for c in commands.call_args_list))

    def test_ambiguous_tethers_require_explicit_selection(self):
        addresses = [self.address("enx-tablet", self.link.local_ip),
                     self.address("usb-other", "192.168.43.50")]
        routes = [{"dev": "enx-tablet", "gateway": self.link.tablet_ip},
                  {"dev": "usb-other", "gateway": "192.168.43.129"}]
        with patch.object(network, "usb_driver", return_value="rndis_host"), \
             patch.object(network, "command", side_effect=self.command_output(addresses, routes)):
            with self.assertRaisesRegex(RuntimeError, "--interface"):
                network.select_link()
            self.assertEqual(network.select_link(interface="enx-tablet"), self.link)

    def test_wrong_route_prevents_socket_creation_and_configuration(self):
        with patch.object(network, "command", return_value=json.dumps([
                {"dev": "wlan0", "gateway": "192.168.1.1"}])) as command, \
             patch.object(network.socket, "socket") as create_socket:
            with self.assertRaisesRegex(RuntimeError, "USB-интерфейс"):
                network.connect(self.link)
            create_socket.assert_not_called()
            with self.assertRaises(RuntimeError):
                network.configure_local_only(self.link)
            self.assertTrue(all(c.args[0] == "ip" for c in command.call_args_list))

    def test_direct_connection_binds_usb_address_before_connecting(self):
        connection = Mock()
        with patch.object(network, "verify_route") as verify, \
             patch.object(network.socket, "socket", return_value=connection) as create_socket:
            self.assertIs(network.connect(self.link), connection)
        verify.assert_called_once_with(self.link)
        create_socket.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
        self.assertLess(connection.mock_calls.index(call.bind((self.link.local_ip, 0))),
                        connection.mock_calls.index(call.connect((self.link.tablet_ip, 27183))))
        connection.close.assert_not_called()

    def test_failed_direct_connection_closes_socket(self):
        connection = Mock()
        connection.connect.side_effect = ConnectionRefusedError("No receiver")
        with patch.object(network, "verify_route"), \
             patch.object(network.socket, "socket", return_value=connection), \
             self.assertRaises(RuntimeError):
            network.connect(self.link)
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
