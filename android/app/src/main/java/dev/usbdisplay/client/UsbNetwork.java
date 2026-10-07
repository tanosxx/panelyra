package dev.usbdisplay.client;

import java.io.IOException;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InterfaceAddress;
import java.net.NetworkInterface;
import java.net.SocketException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Enumeration;
import java.util.List;

/** Finds the Android USB-tether address without opening a listener on Wi-Fi. */
final class UsbNetwork {
    static final class Target {
        final Inet4Address address;
        final int prefixLength;
        final String interfaceName;
        final boolean usb;
        final String discoveryError;

        Target(Inet4Address address, int prefixLength, String interfaceName, boolean usb,
               String discoveryError) {
            this.address = address;
            this.prefixLength = prefixLength;
            this.interfaceName = interfaceName;
            this.usb = usb;
            this.discoveryError = discoveryError;
        }

        boolean allowsPeer(InetAddress peer) {
            if (!(peer instanceof Inet4Address)) {
                return false;
            }
            if (!usb) {
                return peer.isLoopbackAddress();
            }
            return usableUsbAddress(peer, prefixLength) && sameSubnet(address, peer, prefixLength);
        }

        boolean sameBinding(Target other) {
            return usb == other.usb && address.equals(other.address)
                    && prefixLength == other.prefixLength && interfaceName.equals(other.interfaceName);
        }

        String endpoint() {
            return address.getHostAddress() + ":" + VideoProtocol.PORT;
        }

    }

    static Target discover() throws IOException {
        List<Target> candidates = new ArrayList<>();
        String error = null;
        try {
            Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
            while (interfaces != null && interfaces.hasMoreElements()) {
                NetworkInterface network = interfaces.nextElement();
                if (!isUsbInterfaceName(network.getName())) {
                    continue;
                }
                try {
                    if (!network.isUp() || network.isLoopback()) {
                        continue;
                    }
                    for (InterfaceAddress binding : network.getInterfaceAddresses()) {
                        InetAddress address = binding.getAddress();
                        int prefix = binding.getNetworkPrefixLength();
                        if (usableUsbAddress(address, prefix)) {
                            candidates.add(new Target((Inet4Address) address, prefix, network.getName(),
                                    true, null));
                        }
                    }
                } catch (SocketException | RuntimeException e) {
                    error = readableError(e);
                }
            }
        } catch (SocketException | RuntimeException e) {
            // Some pre-API31 Android builds throw NPE for orphan virtual interfaces.
            error = readableError(e);
        }
        if (!candidates.isEmpty()) {
            Collections.sort(candidates, (left, right) -> {
                int byName = left.interfaceName.compareTo(right.interfaceName);
                return byName != 0 ? byName
                        : left.address.getHostAddress().compareTo(right.address.getHostAddress());
            });
            return candidates.get(0);
        }
        return new Target((Inet4Address) InetAddress.getByAddress(new byte[]{127, 0, 0, 1}),
                8, "lo", false, error);
    }

    static boolean isUsbInterfaceName(String name) {
        return name != null && (name.startsWith("rndis") || name.startsWith("usb")
                || name.startsWith("ncm"));
    }

    static boolean usableUsbAddress(InetAddress address, int prefixLength) {
        return address instanceof Inet4Address && prefixLength >= 1 && prefixLength <= 32
                && !address.isAnyLocalAddress() && !address.isLoopbackAddress()
                && !address.isMulticastAddress();
    }

    static boolean sameSubnet(InetAddress local, InetAddress peer, int prefixLength) {
        if (!(local instanceof Inet4Address) || !(peer instanceof Inet4Address)
                || prefixLength < 1 || prefixLength > 32) {
            return false;
        }
        byte[] localBytes = local.getAddress();
        byte[] peerBytes = peer.getAddress();
        for (int i = 0; i < 4; i++) {
            int bits = Math.min(8, Math.max(0, prefixLength - i * 8));
            int mask = (0xff << (8 - bits)) & 0xff;
            if ((localBytes[i] & mask) != (peerBytes[i] & mask)) {
                return false;
            }
        }
        return true;
    }

    private static String readableError(Exception e) {
        return e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName();
    }

    private UsbNetwork() {}
}
