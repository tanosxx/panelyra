package dev.usbdisplay.client;

import android.media.MediaCodec;
import android.media.MediaFormat;
import android.os.Build;
import android.view.Surface;

import java.io.IOException;
import java.nio.ByteBuffer;

/** One input producer, one output consumer; no application-side frame queue. */
final class DecoderPump {
    interface Listener {
        boolean isStopped();
        void onFirstFrame();
        void onDecoderFailure();
    }

    private final Listener listener;
    private final MediaCodec codec;
    private final ByteBuffer[] legacyInputBuffers;
    private final Thread outputThread;
    private volatile boolean outputStopped;
    private volatile IOException failure;

    DecoderPump(VideoProtocol.StreamInfo stream, VideoProtocol.CodecData data,
                Surface surface, Listener listener) throws IOException {
        this.listener = listener;
        MediaCodec created = null;
        try {
            MediaFormat format = MediaFormat.createVideoFormat("video/avc", stream.width, stream.height);
            format.setInteger(MediaFormat.KEY_FRAME_RATE, stream.fps);
            format.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE,
                    Math.min(VideoProtocol.MAX_FRAME,
                            Math.max(256 * 1024, stream.width * stream.height * 3 / 2)));
            format.setByteBuffer("csd-0", ByteBuffer.wrap(data.sps));
            format.setByteBuffer("csd-1", ByteBuffer.wrap(data.pps));
            if (Build.VERSION.SDK_INT >= 30) {
                format.setInteger("low-latency", 1);
            }
            created = MediaCodec.createDecoderByType("video/avc");
            created.configure(format, surface, null, 0);
            created.setVideoScalingMode(MediaCodec.VIDEO_SCALING_MODE_SCALE_TO_FIT);
            created.start();
            codec = created;
            legacyInputBuffers = Build.VERSION.SDK_INT < 21 ? codec.getInputBuffers() : null;
        } catch (IOException | RuntimeException e) {
            if (created != null) {
                try {
                    created.release();
                } catch (RuntimeException ignored) {
                    // Preserve the original configuration error.
                }
            }
            throw new IOException("Could not start the H.264 decoder. Try a lower resolution", e);
        }
        outputThread = new Thread(this::drainOutput, "usb-display-video");
        outputThread.start();
    }

    void queue(VideoProtocol.Frame frame) throws IOException {
        long startedWaiting = System.nanoTime();
        try {
            while (!listener.isStopped() && !outputStopped) {
                checkFailure();
                if (System.nanoTime() - startedWaiting > 3_000_000_000L) {
                    throw new IOException("The decoder has not accepted frames for 3 seconds. "
                            + "Try a lower resolution");
                }
                int index = codec.dequeueInputBuffer(10_000);
                if (index < 0) {
                    continue;
                }
                ByteBuffer input = Build.VERSION.SDK_INT >= 21
                        ? codec.getInputBuffer(index) : legacyInputBuffers[index];
                if (input == null || input.capacity() < frame.length) {
                    throw new IOException("Frame exceeds decoder capacity. Lower the resolution or bitrate");
                }
                input.clear();
                input.put(frame.bytes, 0, frame.length);
                codec.queueInputBuffer(index, 0, frame.length, frame.presentationTimeUs, 0);
                return;
            }
            checkFailure();
            throw new IOException("Playback stopped");
        } catch (RuntimeException e) {
            throw new IOException("Could not queue a frame in the H.264 decoder", e);
        }
    }

    IOException failure() {
        return failure;
    }

    private void checkFailure() throws IOException {
        if (failure != null) {
            throw failure;
        }
    }

    private void drainOutput() {
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        boolean firstFrame = true;
        try {
            while (!outputStopped && !listener.isStopped()) {
                int index = codec.dequeueOutputBuffer(info, 10_000);
                if (index < 0) {
                    continue;
                }
                // Show the newest available output and discard stale decoded frames.
                // This bounds latency without dropping encoded reference frames.
                int newest = index;
                for (int drained = 0; drained < 8 && !outputStopped; drained++) {
                    int next = codec.dequeueOutputBuffer(info, 0);
                    if (next >= 0) {
                        codec.releaseOutputBuffer(newest, false);
                        newest = next;
                    } else if (next == MediaCodec.INFO_TRY_AGAIN_LATER) {
                        break;
                    }
                }
                boolean render = !outputStopped && !listener.isStopped();
                codec.releaseOutputBuffer(newest, render);
                if (render && firstFrame) {
                    firstFrame = false;
                    listener.onFirstFrame();
                }
            }
        } catch (RuntimeException e) {
            if (!outputStopped && !listener.isStopped()) {
                failure = new IOException("H.264 decoder error. Try a lower resolution", e);
                listener.onDecoderFailure();
            }
        }
    }

    /** Called from the connection worker, never from the Android UI thread. */
    void close() {
        outputStopped = true;
        boolean interrupted = false;
        while (outputThread.isAlive()) {
            try {
                outputThread.join();
            } catch (InterruptedException e) {
                interrupted = true;
            }
        }
        try {
            codec.stop();
        } catch (RuntimeException ignored) {
            // Some vendor codecs are already stopped after a decoder/surface error.
        } finally {
            try {
                codec.release();
            } catch (RuntimeException ignored) {
                // No more codec operations take place after release.
            }
        }
        if (interrupted) {
            Thread.currentThread().interrupt();
        }
    }
}
