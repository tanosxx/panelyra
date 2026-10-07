package dev.usbdisplay.client;

import java.io.Closeable;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketTimeoutException;

/** Session-owned language listener, isolated from the video reader and decoder. */
final class LanguageServer implements Runnable, Closeable {
    interface Listener {
        boolean applyLanguage(String language);
    }

    private final Object lock = new Object();
    private final UsbNetwork.Target target;
    private final Listener listener;
    private final ServerSocket server;
    private volatile boolean stopped;
    private Socket connection;

    LanguageServer(UsbNetwork.Target target, Listener listener) throws IOException {
        this.target = target;
        this.listener = listener;
        server = new ServerSocket();
        try {
            server.setReuseAddress(true);
            server.setSoTimeout(1500);
            server.bind(new InetSocketAddress(target.address, LanguageProtocol.PORT), 1);
        } catch (IOException | RuntimeException failure) {
            closeQuietly(server);
            throw failure;
        }
    }

    @Override
    public void run() {
        try {
            while (!stopped) {
                Socket accepted;
                try {
                    accepted = server.accept();
                } catch (SocketTimeoutException waiting) {
                    continue;
                }
                synchronized (lock) {
                    if (stopped || !target.allowsPeer(accepted.getInetAddress())) {
                        closeQuietly(accepted);
                        continue;
                    }
                    connection = accepted;
                }
                try {
                    accepted.setSoTimeout(1500);
                    accepted.setTcpNoDelay(true);
                    String language = LanguageProtocol.read(accepted.getInputStream());
                    if (!stopped && listener.applyLanguage(language) && !stopped) {
                        accepted.getOutputStream().write(LanguageProtocol.acknowledgement(language));
                    }
                } catch (IOException ignored) {
                    // Incomplete or invalid control requests never affect video playback.
                } finally {
                    synchronized (lock) {
                        closeQuietly(accepted);
                        if (connection == accepted) connection = null;
                    }
                }
            }
        } catch (IOException ignored) {
            // Closing the session interrupts accept(). The optional channel cannot stop video.
        } finally {
            close();
        }
    }

    @Override
    public void close() {
        stopped = true;
        synchronized (lock) {
            closeQuietly(connection);
            closeQuietly(server);
            connection = null;
        }
    }

    private static void closeQuietly(Closeable value) {
        if (value != null) {
            try {
                value.close();
            } catch (IOException ignored) {
                // Closing is idempotent, including during a pending read.
            }
        }
    }
}
