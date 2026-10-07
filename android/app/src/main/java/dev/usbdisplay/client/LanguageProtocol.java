package dev.usbdisplay.client;

import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.Charset;

/** Small optional control channel; video remains the original UTD1 byte stream. */
final class LanguageProtocol {
    static final int PORT = 27184;
    static final int REQUEST_SIZE = 7;
    private static final Charset ASCII = Charset.forName("US-ASCII");

    static boolean isSupported(String language) {
        return "en".equals(language) || "ru".equals(language);
    }

    /** The sender half-closes its output so extra bytes cannot be mistaken for a valid request. */
    static String read(InputStream input) throws IOException {
        byte[] request = new byte[REQUEST_SIZE];
        new DataInputStream(input).readFully(request);
        if (input.read() != -1) {
            throw new IOException("Unexpected language request data");
        }
        String value = new String(request, ASCII);
        if (!value.startsWith("PLYL1") || !isSupported(value.substring(5))) {
            throw new IOException("Invalid language request");
        }
        return value.substring(5);
    }

    static byte[] acknowledgement(String language) {
        if (!isSupported(language)) {
            throw new IllegalArgumentException("Unsupported language");
        }
        return ("PLYL1" + language).getBytes(ASCII);
    }

    /** Stored PC preference wins; without one Android's own locale selects its resources. */
    static String selectLanguage(String storedLanguage, String systemLanguage) {
        if (isSupported(storedLanguage)) return storedLanguage;
        return "ru".equals(systemLanguage) ? "ru" : "en";
    }

    private LanguageProtocol() {}
}
