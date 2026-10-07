"""Read-only discovery for the device panel, without contacting the receiver.

``usb-ready`` means that a USB tether and its peer address are known. It does
not imply that Panelyra is running on Android. Discovery never opens the video
socket, starts ADB, or changes the host's network configuration.
"""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal

from . import network


DeviceState = Literal["usb-ready", "usb-no-address", "usb-disabled"]


@dataclass(frozen=True)
class Device:
    name: str
    transport: str
    state: DeviceState
    interface: str | None = None
    tablet_ip: str | None = None


def _text(path):
    try:
        # Device descriptors are supplied by USB hardware. Keep them short and
        # single-line, and never read the device's serial-number attribute.
        value = path.read_text(errors="replace")[:160]
        return " ".join("".join(c for c in value if c.isprintable() or c.isspace()).split())
    except OSError:
        return ""  # A device may disappear while sysfs is being enumerated.


def _children(path):
    try:
        return sorted(path.iterdir())
    except OSError:
        return []


def _usb_parent(path):
    try:
        device = path.resolve()
    except (OSError, RuntimeError):
        return None
    for parent in (device, *device.parents):
        # If the endpoint disappears during enumeration, walking its old path
        # must not identify a still-connected upstream hub as the tablet.
        if _text(parent / "bDeviceClass").lower() == "09":
            return None
        if (parent / "idVendor").is_file() and (parent / "idProduct").is_file():
            return parent
    return None


def _name(device):
    manufacturer = _text(device / "manufacturer")
    product = _text(device / "product")
    if not product:
        return manufacturer or "USB"
    if not manufacturer or re.search(r"(?<!\w)" + re.escape(manufacturer) + r"(?!\w)", product, re.I):
        return product
    return f"{manufacturer} {product}"


def _android_device(device):
    description = " ".join(_text(device / field) for field in ("manufacturer", "product"))
    # A still-image interface is also used by cameras. Require an Android or
    # tablet identity as well; a Lenovo manufacturer string alone is not enough.
    android_identity = re.search(r"\bandroid\b|\btablet\b|\btab\b|\btb-[a-z0-9]+", description, re.I)
    for interface in _children(device):
        values = tuple(_text(interface / attribute).lower() for attribute in (
            "bInterfaceClass", "bInterfaceSubClass", "bInterfaceProtocol"))
        if values == ("ff", "42", "01"):  # Android Debug Bridge USB interface.
            return True
        if values == ("06", "01", "01") and android_identity:
            return True
    return False


def discover_devices(*, sys_net_root=Path("/sys/class/net"),
                     sys_usb_root=Path("/sys/bus/usb/devices"), links=None):
    """Return every matching physical USB device, with tethers listed first.

    ``links`` can supply an iterable of :class:`network.UsbLink` records for
    tests. When omitted, the existing read-only network discovery is used; its
    errors propagate so the UI can distinguish failure from an empty result.
    Both sysfs roots are injectable. Missing/disconnected sysfs entries are
    skipped. Multiple devices remain separate; this function never selects one
    for streaming.
    """
    sys_net_root, sys_usb_root = Path(sys_net_root), Path(sys_usb_root)
    if links is None:
        links = network.discover_links()
    addresses = {}
    for link in links:
        addresses.setdefault(link.interface, []).append(link)

    devices = {}
    priority = {"usb-ready": 0, "usb-no-address": 1, "usb-disabled": 2}
    for entry in _children(sys_net_root):
        interface = entry.name
        try:
            driver = (entry / "device" / "driver").resolve().name
        except (OSError, RuntimeError):
            continue
        if driver != "rndis_host":
            continue
        physical = _usb_parent(entry / "device")
        if physical is None:
            continue
        # There can be more than one IPv4 address or interface on one physical
        # device. Prefer an address with a known peer, without duplicating it.
        tablet_ip = next((link.tablet_ip for link in addresses.get(interface, [])
                          if link.tablet_ip), None)
        state = "usb-ready" if tablet_ip else "usb-no-address"
        device = Device(_name(physical), "usb", state, interface, tablet_ip)
        previous = devices.get(physical)
        if previous is None or priority[state] < priority[previous.state]:
            devices[physical] = device

    for entry in _children(sys_usb_root):
        # The bus directory contains both devices and their USB interfaces.
        # Inspect only physical devices, not an interface's ancestor again.
        if not (entry / "idVendor").is_file() or not (entry / "idProduct").is_file():
            continue
        physical = _usb_parent(entry)
        if physical is not None and physical not in devices and _android_device(physical):
            devices[physical] = Device(_name(physical), "usb", "usb-disabled")

    return sorted(devices.values(), key=lambda device: (
        priority[device.state], device.name.casefold(), device.interface or ""))
