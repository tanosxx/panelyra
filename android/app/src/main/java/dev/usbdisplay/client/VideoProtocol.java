package dev.usbdisplay.client;

import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;

/** Small bounded wire parser. All integers on the wire are big-endian. */
final class VideoProtocol {
    static final int PORT = 27183;
    static final int MAX_FRAME = 8 * 1024 * 1024;
    private static final int MAGIC = 0x55544431; // UTD1

    static final class StreamInfo {
        final int width;
        final int height;
        final int fps;

        StreamInfo(int width, int height, int fps) {
            this.width = width;
            this.height = height;
            this.fps = fps;
        }
    }

    static final class Frame {
        byte[] bytes = new byte[256 * 1024];
        int length;
        long presentationTimeUs;
    }

    static final class Reader {
        private final DataInputStream input;
        private final Frame frame = new Frame();
        private long lastTimestamp = -1;

        Reader(InputStream input) {
            this.input = new DataInputStream(input);
        }

        StreamInfo readHeader() throws IOException {
            if (input.readInt() != MAGIC) {
                throw new IOException("Incompatible protocol: expected UTD1");
            }
            int width = input.readInt();
            int height = input.readInt();
            int fps = input.readInt();
            if (!validDimension(width) || !validDimension(height) || fps < 5 || fps > 60) {
                throw new IOException("Invalid dimensions or frame rate in the header");
            }
            return new StreamInfo(width, height, fps);
        }

        /** Storage belongs to the reader and is reused on the next read. */
        Frame readFrame() throws IOException {
            int length = input.readInt();
            long timestamp = input.readLong();
            if (length <= 0 || length > MAX_FRAME) {
                throw new IOException("H.264 frame size outside the allowed range");
            }
            if (timestamp < 0 || timestamp < lastTimestamp) {
                throw new IOException("Invalid frame timestamp");
            }
            if (frame.bytes.length < length) {
                int capacity = frame.bytes.length;
                while (capacity < length) {
                    capacity = Math.min(MAX_FRAME, capacity * 2);
                }
                frame.bytes = new byte[capacity];
            }
            input.readFully(frame.bytes, 0, length);
            frame.length = length;
            frame.presentationTimeUs = timestamp;
            lastTimestamp = timestamp;
            return frame;
        }
    }

    static final class CodecData {
        final byte[] sps;
        final byte[] pps;

        CodecData(byte[] sps, byte[] pps) {
            this.sps = sps;
            this.pps = pps;
        }
    }

    /** Extract CSD and remove initial SPS/PPS from this access unit in place. */
    static CodecData parseInitialFrame(Frame frame) throws IOException {
        byte[] sps = null;
        byte[] pps = null;
        boolean hasIdr = false;
        int outputLength = 0;
        int start = findStartCode(frame.bytes, 0, frame.length);
        if (start < 0 || start > 4) {
            throw new IOException("Expected H.264 Annex B");
        }
        for (int i = 0; i < start; i++) {
            if (frame.bytes[i] != 0) {
                throw new IOException("Invalid H.264 Annex B prefix");
            }
        }
        while (start >= 0) {
            int payload = start + (frame.bytes[start + 2] == 1 ? 3 : 4);
            int next = findStartCode(frame.bytes, payload, frame.length);
            int end = next >= 0 ? next : frame.length;
            if (payload >= end || (frame.bytes[payload] & 0x80) != 0) {
                throw new IOException("Invalid H.264 NAL");
            }
            int type = frame.bytes[payload] & 0x1f;
            if (type == 7 && sps == null) {
                sps = copyCodecData(frame.bytes, payload, end);
            } else if (type == 8 && pps == null) {
                pps = copyCodecData(frame.bytes, payload, end);
            } else if (type == 5) {
                hasIdr = true;
            }
            if (type != 7 && type != 8) {
                int nalLength = end - start;
                System.arraycopy(frame.bytes, start, frame.bytes, outputLength, nalLength);
                outputLength += nalLength;
            }
            start = next;
        }
        if (sps == null || pps == null || !hasIdr) {
            throw new IOException("The first frame must contain SPS, PPS and an IDR keyframe");
        }
        // MediaCodec submits csd-0/csd-1 itself on start(); don't submit them twice.
        frame.length = outputLength;
        return new CodecData(sps, pps);
    }

    private static byte[] copyCodecData(byte[] data, int start, int end) throws IOException {
        if (end - start > 65536) {
            throw new IOException("H.264 parameter set too large");
        }
        byte[] result = new byte[end - start + 4];
        result[0] = 0;
        result[1] = 0;
        result[2] = 0;
        result[3] = 1;
        System.arraycopy(data, start, result, 4, end - start);
        return result;
    }

    private static int findStartCode(byte[] data, int from, int length) {
        for (int i = from; i + 2 < length; i++) {
            if (data[i] == 0 && data[i + 1] == 0
                    && (data[i + 2] == 1
                    || (i + 3 < length && data[i + 2] == 0 && data[i + 3] == 1))) {
                return i;
            }
        }
        return -1;
    }

    private static boolean validDimension(int dimension) {
        return dimension >= 160 && dimension <= 2560 && (dimension & 1) == 0;
    }

    private VideoProtocol() {}
}
