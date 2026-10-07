package dev.usbdisplay.client;

import java.io.DataInputStream;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.Socket;
import java.nio.charset.Charset;
import java.util.Arrays;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Optional real-loopback regression: PANELYRA_TEST_NETWORK=1 android/tests/run.sh. */
public final class LanguageServerTest {
    private static int assertions;
    private static final Charset ASCII = Charset.forName("US-ASCII");

    public static void main(String[] args) throws Exception {
        Inet4Address address = (Inet4Address) InetAddress.getByAddress(new byte[]{127, 0, 0, 1});
        UsbNetwork.Target target = new UsbNetwork.Target(address, 8, "lo", false, null);
        AtomicReference<String> language = new AtomicReference<>();
        LanguageServer server = new LanguageServer(target, selected -> {
            language.set(selected);
            return true;
        });
        Thread worker = new Thread(server, "language-test");
        worker.start();
        try {
            request("PLYL1en", true);
            check("en".equals(language.get()), "valid English applied");
            for (String invalid : new String[]{"PLYL1r", "PLYL1de", "PLYL1ruX", "PLYL2ru"}) {
                request(invalid, false);
                check("en".equals(language.get()), "invalid request did not change language");
            }
            try (Socket stalled = connect()) {
                stalled.getOutputStream().write("PLYL1ru".getBytes(ASCII));
                check(stalled.getInputStream().read() == -1, "missing EOF times out");
                check("en".equals(language.get()), "timeout did not change language");
            }
            request("PLYL1ru", true);
            check("ru".equals(language.get()), "listener survives malformed and stalled peers");
            try (Socket incomplete = connect()) {
                incomplete.getOutputStream().write('P');
                server.close();
                worker.join(2000);
                check(!worker.isAlive(), "closing interrupts pending read or accept");
            }
        } finally {
            server.close();
            worker.join(2000);
        }
        // Simulate Activity/USB binding restart; neither old socket may retain the port.
        CountDownLatch callback = new CountDownLatch(1);
        LanguageServer restarted = new LanguageServer(target, selected -> {
            callback.countDown();
            return false;
        });
        Thread restartedWorker = new Thread(restarted, "language-restart-test");
        restartedWorker.start();
        try {
            request("PLYL1ru", false);
            check(callback.await(2, TimeUnit.SECONDS), "rebound listener accepts requests");
        } finally {
            restarted.close();
            restartedWorker.join(2000);
        }
        check(!restartedWorker.isAlive(), "closing interrupts pending accept");
        System.out.println("LanguageServer: " + assertions + " assertions passed");
    }

    private static Socket connect() throws Exception {
        Socket socket = new Socket("127.0.0.1", LanguageProtocol.PORT);
        socket.setSoTimeout(3000);
        return socket;
    }

    private static void request(String value, boolean acknowledged) throws Exception {
        try (Socket socket = connect()) {
            byte[] request = value.getBytes(ASCII);
            socket.getOutputStream().write(request);
            socket.shutdownOutput();
            if (acknowledged) {
                byte[] reply = new byte[LanguageProtocol.REQUEST_SIZE];
                new DataInputStream(socket.getInputStream()).readFully(reply);
                check(Arrays.equals(request, reply), "exact matching acknowledgement");
            }
            check(socket.getInputStream().read() == -1, "connection closes after one request");
        }
    }

    private static void check(boolean success, String message) {
        if (!success) throw new AssertionError(message);
        assertions++;
    }
}
