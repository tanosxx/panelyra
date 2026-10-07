"""Direct TCP over an Android USB tether, without ADB or a Wi-Fi listener."""

from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import time

USB_DRIVERS = {"rndis_host", "cdc_ncm", "cdc_ether"}


def command(*args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Не удалось выполнить {args[0]}: {error}") from error
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Ошибка {args[0]}")
    return result.stdout


@dataclass(frozen=True)
class UsbLink:
    interface: str
    local_ip: str
    prefix: int
    tablet_ip: str | None

    @property
    def network(self):
        return ipaddress.IPv4Network(f"{self.local_ip}/{self.prefix}", strict=False)

    def validate_peer(self, address):
        try:
            peer = ipaddress.IPv4Address(address)
        except ipaddress.AddressValueError as error:
            raise ValueError("Нужен числовой IPv4-адрес планшета") from error
        if (peer not in self.network or peer.is_loopback or peer.is_multicast
                or peer.is_unspecified or str(peer) == self.local_ip
                or peer in (self.network.network_address, self.network.broadcast_address)):
            raise ValueError(f"Адрес планшета должен принадлежать USB-подсети {self.network}")
        return str(peer)

    def require_tablet(self):
        if not self.tablet_ip:
            raise RuntimeError("Не удалось определить адрес планшета. Укажите --tablet-ip с экрана приложения")
        return self.validate_peer(self.tablet_ip)


def usb_driver(interface, sys_root=Path("/sys/class/net")):
    device = (sys_root / interface / "device").resolve()
    driver = (device / "driver").resolve().name
    if driver not in USB_DRIVERS:
        return None
    if not any((parent / "idVendor").is_file() for parent in (device, *device.parents)):
        return None
    return driver


def dhcp_gateway(options):
    # nmcli -g returns options separated by " | ", with escaped MAC colons.
    for key in ("routers", "dhcp_server_identifier"):
        match = re.search(r"(?:^|[|\n])\s*" + key + r"\s*=\s*([0-9.]+)", options)
        if match:
            return match.group(1)
    return None


def discover_links(interface=None, tablet_ip=None):
    addresses = json.loads(command("ip", "-j", "-4", "address", "show"))
    routes = json.loads(command("ip", "-j", "-4", "route", "show"))
    links = []
    for device in addresses:
        name = device["ifname"]
        driver = usb_driver(name)
        if (interface and interface != name) or not driver:
            continue
        # Auto-detection targets Android RNDIS. A USB Ethernet adapter also
        # uses cdc_ether/ncm; only use those when explicitly selected.
        if not interface and driver != "rndis_host":
            continue
        if "UP" not in device.get("flags", []):
            continue
        gateway = next((r["gateway"] for r in routes
                        if r.get("dev") == name and r.get("gateway")), None)
        if not gateway and not tablet_ip:
            try:
                gateway = dhcp_gateway(command("nmcli", "-g", "DHCP4.OPTION", "device", "show", name))
            except RuntimeError:
                pass  # Explicit --tablet-ip works without NetworkManager.
        for address in device.get("addr_info", []):
            if address.get("family") != "inet" or address.get("scope") != "global":
                continue
            link = UsbLink(name, address["local"], address["prefixlen"], tablet_ip or gateway)
            if link.tablet_ip:
                try:
                    link.require_tablet()
                except ValueError:
                    if tablet_ip:
                        continue
                    link = UsbLink(name, address["local"], address["prefixlen"], None)
            links.append(link)
    return links


def select_link(interface=None, tablet_ip=None):
    links = discover_links(interface, tablet_ip)
    if not links:
        raise RuntimeError("USB-модем не найден. Включите его на планшете и дождитесь подключения Ubuntu")
    if len(links) != 1:
        names = ", ".join(f"{link.interface} ({link.local_ip})" for link in links)
        raise RuntimeError(f"Найдено несколько USB-адресов: {names}. Укажите --interface и --tablet-ip")
    return links[0]


def verify_route(link):
    tablet = link.require_tablet()
    routes = json.loads(command("ip", "-j", "-4", "route", "get", tablet, "from", link.local_ip))
    if not routes or routes[0].get("dev") != link.interface:
        raise RuntimeError("Маршрут к планшету проходит не через выбранный USB-интерфейс")


def configure_local_only(link):
    """Change only live USB settings, never the saved NM connection profile."""
    verify_route(link)
    command("nmcli", "device", "modify", link.interface,
            "ipv4.never-default", "yes", "ipv6.never-default", "yes",
            "ipv4.ignore-auto-dns", "yes", "ipv6.ignore-auto-dns", "yes")


def connect(link, wait_seconds=0):
    tablet = link.require_tablet()
    deadline = time.monotonic() + wait_seconds
    print(f"USB-модем: {link.interface}, ПК {link.local_ip}, планшет {tablet}:27183", flush=True)
    print("Откройте Panelyra на планшете. При необходимости нажмите «Обновить подключение».", flush=True)
    while True:
        verify_route(link)
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            connection.settimeout(3)
            connection.bind((link.local_ip, 0))
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
            connection.connect((tablet, 27183))
            return connection
        except OSError as error:
            connection.close()
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Приёмник {tablet}:27183 недоступен. Установите новый APK и откройте приложение") from error
            time.sleep(0.5)
