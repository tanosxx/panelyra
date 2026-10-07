package dev.usbdisplay.client;

import java.net.Inet4Address;
import java.net.InetAddress;

/** Checks interface allowlisting and peer/subnet boundaries without an Android runtime. */
public final class UsbNetworkTest {
    private static int assertions;

    public static void main(String[] args) throws Exception {
        for (String name : new String[]{"rndis0", "rndis1", "usb0", "usb1", "ncm0", "ncm_usb0"}) {
            check(UsbNetwork.isUsbInterfaceName(name), "USB name " + name);
        }
        for (String name : new String[]{"wlan0", "wifi0", "ap0", "eth0", "en0", "rmnet0", "lo", "tun0", "", null}) {
            check(!UsbNetwork.isUsbInterfaceName(name), "unproven USB name " + name);
        }
        Inet4Address tablet = ipv4(192, 168, 42, 129);
        Inet4Address pc = ipv4(192, 168, 42, 58);
        UsbNetwork.Target usb = new UsbNetwork.Target(tablet, 24, "rndis0", true, null);
        check(usb.allowsPeer(pc), "actual Lenovo USB subnet");
        check(!usb.allowsPeer(ipv4(192, 168, 43, 58)), "adjacent subnet rejected");
        check(!usb.allowsPeer(ipv4(10, 0, 0, 58)), "different private network rejected");
        check(!usb.allowsPeer(ipv4(127, 0, 0, 1)), "loopback rejected on USB listener");
        check(!usb.allowsPeer(ipv4(0, 0, 0, 0)), "wildcard rejected");
        check(!usb.allowsPeer(ipv4(224, 0, 0, 1)), "multicast rejected");
        InetAddress ipv6 = InetAddress.getByAddress(new byte[16]);
        check(!usb.allowsPeer(ipv6), "IPv6 rejected");
        check(!UsbNetwork.usableUsbAddress(ipv6, 64), "IPv6 interface skipped");
        check(!UsbNetwork.usableUsbAddress(tablet, 0), "missing prefix skipped");
        check(!UsbNetwork.usableUsbAddress(tablet, -1), "invalid prefix skipped");
        check(!UsbNetwork.usableUsbAddress(tablet, 33), "oversized IPv4 prefix skipped");

        check(!UsbNetwork.sameSubnet(tablet, pc, 25), "partial-byte mask separates halves");
        check(UsbNetwork.sameSubnet(tablet, ipv4(192, 168, 42, 254), 25), "partial-byte mask matches");
        check(UsbNetwork.sameSubnet(tablet, ipv4(192, 168, 42, 128), 31), "/31 adjacent peer");
        check(!UsbNetwork.sameSubnet(tablet, ipv4(192, 168, 42, 130), 31), "/31 next network rejected");
        check(UsbNetwork.sameSubnet(tablet, tablet, 32), "/32 exact address");
        check(!UsbNetwork.sameSubnet(tablet, pc, 32), "/32 other address rejected");
        check(UsbNetwork.sameSubnet(tablet, ipv4(128, 0, 0, 1), 1), "/1 high-bit match");
        check(!UsbNetwork.sameSubnet(tablet, ipv4(126, 0, 0, 1), 1), "/1 high-bit mismatch");
        check(!UsbNetwork.sameSubnet(tablet, pc, 0), "never allow global /0");

        UsbNetwork.Target adb = new UsbNetwork.Target(ipv4(127, 0, 0, 1), 8, "lo", false, null);
        check(adb.allowsPeer(ipv4(127, 0, 0, 1)), "ADB localhost accepted");
        check(adb.allowsPeer(ipv4(127, 0, 0, 2)), "ADB loopback subnet accepted");
        check(!adb.allowsPeer(pc), "ADB rejects USB address");
        check(!adb.allowsPeer(ipv6), "ADB listener is IPv4 only");
        check(usb.sameBinding(new UsbNetwork.Target(tablet, 24, "rndis0", true, null)), "same binding stable");
        check(!usb.sameBinding(new UsbNetwork.Target(tablet, 25, "rndis0", true, null)), "netmask change detected");
        check(!usb.sameBinding(new UsbNetwork.Target(pc, 24, "rndis0", true, null)), "address change detected");
        check(!usb.sameBinding(adb), "ADB-to-USB transition detected");
        check(usb.endpoint().contains("192.168.42.129:27183"), "address visible in USB UI");
        System.out.println("UsbNetwork: " + assertions + " assertions passed");
    }

    private static Inet4Address ipv4(int a, int b, int c, int d) throws Exception {
        return (Inet4Address) InetAddress.getByAddress(new byte[]{(byte) a, (byte) b, (byte) c, (byte) d});
    }

    private static void check(boolean success, String message) {
        if (!success) {
            throw new AssertionError(message);
        }
        assertions++;
    }
}
