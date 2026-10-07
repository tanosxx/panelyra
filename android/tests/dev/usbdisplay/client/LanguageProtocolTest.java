package dev.usbdisplay.client;

import java.io.ByteArrayInputStream;
import java.io.FilterInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.SocketTimeoutException;
import java.nio.charset.Charset;
import java.util.Arrays;

/** Language framing and first-run locale behavior without an Android runtime. */
public final class LanguageProtocolTest {
    private static final Charset ASCII = Charset.forName("US-ASCII");
    private static int assertions;

    public static void main(String[] args) throws Exception {
        for (String language : new String[]{"en", "ru"}) {
            byte[] request = ("PLYL1" + language).getBytes(ASCII);
            check(LanguageProtocol.read(new ByteArrayInputStream(request)).equals(language), "valid " + language);
            InputStream fragmented = new FilterInputStream(new ByteArrayInputStream(request)) {
                @Override
                public int read(byte[] bytes, int offset, int count) throws IOException {
                    return super.read(bytes, offset, Math.min(1, count));
                }
            };
            check(LanguageProtocol.read(fragmented).equals(language), "fragmented " + language);
            check(Arrays.equals(request, LanguageProtocol.acknowledgement(language)), "matching acknowledgement");
            for (int length = 0; length < request.length; length++) {
                rejects(Arrays.copyOf(request, length), "truncated request " + length);
            }
            rejects(Arrays.copyOf(request, request.length + 1), "extra byte rejected");
            rejects(("PLYL1" + language + "PLYL1en").getBytes(ASCII), "multiple commands rejected");
        }
        for (String request : new String[]{"UTD1een", "PLYL2en", "PLYL1de", "PLYL1EN", "PLYL1  "}) {
            rejects(request.getBytes(ASCII), "invalid command " + request);
        }
        rejects(new byte[]{80, 76, 89, 76, 49, (byte) 0xff, (byte) 0xff}, "non-ASCII rejected");
        byte[] valid = "PLYL1ru".getBytes(ASCII);
        InputStream stalled = new ByteArrayInputStream(valid) {
            @Override
            public synchronized int read() {
                // The socket read after seven bytes must still wait for a real EOF.
                return 0;
            }
        };
        try {
            LanguageProtocol.read(stalled);
            throw new AssertionError("request without EOF accepted");
        } catch (IOException expected) {
            assertions++;
        }
        try {
            LanguageProtocol.read(new InputStream() {
                @Override
                public int read() throws IOException { throw new SocketTimeoutException("test"); }
            });
            throw new AssertionError("timeout swallowed");
        } catch (SocketTimeoutException expected) {
            assertions++;
        }
        for (String language : new String[]{null, "", "auto", "de", "RU", "ru-RU"}) {
            check(!LanguageProtocol.isSupported(language), "unsupported wire/stored value");
            check(LanguageProtocol.selectLanguage(language, "ru").equals("ru"), "system Russian fallback");
            check(LanguageProtocol.selectLanguage(language, "en").equals("en"), "system English fallback");
        }
        check(LanguageProtocol.selectLanguage(null, "de").equals("en"), "other system language uses English");
        check(LanguageProtocol.selectLanguage("ru", "en").equals("ru"), "stored Russian overrides system English");
        check(LanguageProtocol.selectLanguage("en", "ru").equals("en"), "stored English overrides system Russian");
        try {
            LanguageProtocol.acknowledgement("auto");
            throw new AssertionError("invalid acknowledgement");
        } catch (IllegalArgumentException expected) {
            assertions++;
        }
        System.out.println("LanguageProtocol: " + assertions + " assertions passed");
    }

    private static void rejects(byte[] value, String message) throws Exception {
        try {
            LanguageProtocol.read(new ByteArrayInputStream(value));
            throw new AssertionError(message);
        } catch (IOException expected) {
            assertions++;
        }
    }

    private static void check(boolean success, String message) {
        if (!success) throw new AssertionError(message);
        assertions++;
    }
}
