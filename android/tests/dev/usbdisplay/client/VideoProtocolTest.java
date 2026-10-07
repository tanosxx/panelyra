package dev.usbdisplay.client;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** Standalone protocol regression checks; no Android runtime or JUnit needed. */
public final class VideoProtocolTest {
    private static int assertions;

    public static void main(String[] args) throws Exception {
        byte[] aud = {0, 0, 1, 9, 0x10};
        byte[] sps = {0, 0, 0, 1, 0x67, 0x42, 0, 0x1e};
        byte[] pps = {0, 0, 1, 0x68, 0x12, 0x34};
        byte[] sei = {0, 0, 0, 1, 6, 5, 1, 2};
        byte[] idr = {0, 0, 1, 0x65, 0x23, 0x45};
        byte[] idrSlice = {0, 0, 0, 1, 0x65, 0x51, 0x52};
        byte[] initial = concat(aud, sps, pps, sei, idr, idrSlice);

        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        DataOutputStream wire = new DataOutputStream(bytes);
        header(wire, 1280, 800, 20);
        packet(wire, initial, 1234567890123L);
        packet(wire, idr, 1234567940123L);
        VideoProtocol.Reader reader = new VideoProtocol.Reader(new ByteArrayInputStream(bytes.toByteArray()) {
            @Override
            public synchronized int read(byte[] b, int off, int len) {
                return super.read(b, off, Math.min(2, len));
            }
        });
        VideoProtocol.StreamInfo stream = reader.readHeader();
        check(stream.width == 1280 && stream.height == 800 && stream.fps == 20, "stream header");
        VideoProtocol.Frame frame = reader.readFrame();
        check(frame.presentationTimeUs == 1234567890123L, "64-bit big-endian timestamp");
        VideoProtocol.CodecData codec = VideoProtocol.parseInitialFrame(frame);
        check(Arrays.equals(codec.sps, sps), "SPS CSD");
        check(Arrays.equals(codec.pps, new byte[]{0, 0, 0, 1, 0x68, 0x12, 0x34}), "PPS prefix normalization");
        check(Arrays.equals(Arrays.copyOf(frame.bytes, frame.length), concat(aud, sei, idr, idrSlice)),
                "in-place CSD removal preserves AUD, SEI and multiple IDR slices");
        check(reader.readFrame().presentationTimeUs == 1234567940123L, "fragmented next packet");
        reject(reader::readFrame, "EOF");

        for (int[] invalid : new int[][]{{159, 800, 20}, {1281, 800, 20}, {1280, 2562, 20},
                {1280, 800, 4}, {1280, 800, 61}, {-2, 800, 20}}) {
            ByteArrayOutputStream bad = new ByteArrayOutputStream();
            header(new DataOutputStream(bad), invalid[0], invalid[1], invalid[2]);
            reject(() -> reader(bad.toByteArray()).readHeader(), "invalid dimensions/fps");
        }
        reject(() -> reader(new byte[16]).readHeader(), "bad magic");
        reject(() -> reader(new byte[]{0x55, 0x54, 0x44, 0x31, 0}).readHeader(), "truncated header");

        for (int invalidLength : new int[]{0, -1, VideoProtocol.MAX_FRAME + 1}) {
            ByteArrayOutputStream bad = new ByteArrayOutputStream();
            DataOutputStream out = new DataOutputStream(bad);
            out.writeInt(invalidLength);
            out.writeLong(0);
            reject(() -> reader(bad.toByteArray()).readFrame(), "invalid frame length");
        }
        ByteArrayOutputStream reversed = new ByteArrayOutputStream();
        DataOutputStream timestamps = new DataOutputStream(reversed);
        packet(timestamps, idr, 10);
        packet(timestamps, idr, 9);
        VideoProtocol.Reader backwards = reader(reversed.toByteArray());
        backwards.readFrame();
        reject(backwards::readFrame, "backwards timestamp");

        ByteArrayOutputStream unsigned = new ByteArrayOutputStream();
        packet(new DataOutputStream(unsigned), idr, -1);
        reject(() -> reader(unsigned.toByteArray()).readFrame(), "unsupported unsigned timestamp");

        ByteArrayOutputStream truncated = new ByteArrayOutputStream();
        packet(new DataOutputStream(truncated), initial, 0);
        byte[] incomplete = Arrays.copyOf(truncated.toByteArray(), truncated.size() - 1);
        reject(() -> reader(incomplete).readFrame(), "truncated payload");
        reject(() -> VideoProtocol.parseInitialFrame(frame(concat(sps, pps))), "missing IDR");
        reject(() -> VideoProtocol.parseInitialFrame(frame(idr)), "missing CSD");
        reject(() -> VideoProtocol.parseInitialFrame(frame(new byte[]{0, 0, 1})), "empty NAL");
        byte[] hugeSps = new byte[65542];
        Arrays.fill(hugeSps, (byte) 0x55);
        hugeSps[0] = hugeSps[1] = hugeSps[2] = 0;
        hugeSps[3] = 1;
        hugeSps[4] = 0x67;
        reject(() -> VideoProtocol.parseInitialFrame(frame(concat(hugeSps, pps, idr))), "oversized CSD");
        if (args.length == 1) {
            byte[] real = Files.readAllBytes(Paths.get(args[0]));
            List<Integer> originalTypes = nalTypes(real, real.length);
            check(originalTypes.contains(7) && originalTypes.contains(8) && originalTypes.contains(5),
                    "real initial AU contains SPS/PPS/IDR");
            VideoProtocol.Frame actual = frame(real);
            VideoProtocol.CodecData csd = VideoProtocol.parseInitialFrame(actual);
            check(csd.sps.length > 5 && csd.pps.length > 5, "real CSD nonempty");
            List<Integer> remainingTypes = nalTypes(actual.bytes, actual.length);
            check(!remainingTypes.contains(7) && !remainingTypes.contains(8) && remainingTypes.contains(5),
                    "real IDR retained and SPS/PPS removed");
            originalTypes.removeIf(type -> type == 7 || type == 8);
            check(remainingTypes.equals(originalTypes), "real access unit NAL order/count preserved");
            System.out.println("Real access unit: " + real.length + " bytes, remaining NALs " + remainingTypes);
        }
        System.out.println("VideoProtocol: " + assertions + " assertions passed");
    }

    private static List<Integer> nalTypes(byte[] bytes, int length) {
        List<Integer> types = new ArrayList<>();
        for (int i = 2; i + 1 < length; i++) {
            if (bytes[i - 2] == 0 && bytes[i - 1] == 0 && bytes[i] == 1) {
                types.add(bytes[i + 1] & 31);
            }
        }
        return types;
    }

    private static VideoProtocol.Reader reader(byte[] bytes) {
        return new VideoProtocol.Reader(new ByteArrayInputStream(bytes));
    }

    private static VideoProtocol.Frame frame(byte[] bytes) {
        VideoProtocol.Frame frame = new VideoProtocol.Frame();
        frame.bytes = bytes;
        frame.length = bytes.length;
        return frame;
    }

    private static void header(DataOutputStream out, int width, int height, int fps) throws IOException {
        out.writeInt(0x55544431);
        out.writeInt(width);
        out.writeInt(height);
        out.writeInt(fps);
    }

    private static void packet(DataOutputStream out, byte[] bytes, long timestamp) throws IOException {
        out.writeInt(bytes.length);
        out.writeLong(timestamp);
        out.write(bytes);
    }

    private static byte[] concat(byte[]... parts) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        for (byte[] part : parts) out.write(part);
        return out.toByteArray();
    }

    private static void check(boolean result, String name) {
        if (!result) throw new AssertionError(name);
        assertions++;
    }

    private static void reject(Checked action, String name) throws Exception {
        try {
            action.run();
        } catch (IOException expected) {
            assertions++;
            return;
        }
        throw new AssertionError("Accepted " + name);
    }

    private interface Checked {
        void run() throws Exception;
    }
}
