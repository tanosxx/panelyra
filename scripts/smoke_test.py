#!/usr/bin/env python3
"""Encode -> UTD1 socket -> H.264 decode. No files or screenshots saved.

Default: synthetic picture. --virtual-screen: temporarily create a GNOME
monitor and verify its removal. Requires the user's desktop session.
"""

import argparse
import math
from pathlib import Path
import socket
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from usbdisplay.host import load_gi
from usbdisplay.resume import LockResumingStream as Stream
from usbdisplay.protocol import HELLO, FRAME, MAGIC, MAX_FRAME, validate_mode


def receive_exact(connection, count):
    chunks = bytearray()
    while len(chunks) < count:
        data = connection.recv(count - len(chunks))
        if not data:
            if not chunks:
                return None
            raise EOFError("Frame truncated")
        chunks.extend(data)
    return bytes(chunks)


def nal_types(payload):
    import re
    return [unit[0] & 31 for unit in re.split(b"\x00\x00\x00?\x01", payload) if unit]


def monitor_names(Gio):
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    state = connection.call_sync(
        "org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
        "org.gnome.Mutter.DisplayConfig", "GetCurrentState", None, None,
        Gio.DBusCallFlags.NONE, 5000, None).unpack()
    return sorted(monitor[0][0] for monitor in state[1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--virtual-screen", action="store_true")
    parser.add_argument("--lock-cycle", action="store_true",
                        help="simulate two lock/unlock cycles with synthetic video (19 seconds)")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--capture-fps", type=int, default=None,
                        help="source/monitor rate, between output fps and 60")
    parser.add_argument("--bitrate", type=int, default=2500, help="H.264 bitrate in kbit/s")
    parser.add_argument("--quality", type=int, default=None,
                        help="constant quantizer 10..35; omitted keeps bitrate mode")
    parser.add_argument("--duration", type=float, default=4,
                        help="positive test duration in seconds (default: 4)")
    parser.add_argument("--disconnect-after", type=int, default=0,
                        help="imitate a tablet disconnect after N frames")
    args = parser.parse_args()
    if args.lock_cycle:
        if args.virtual_screen or args.disconnect_after:
            parser.error("--lock-cycle uses only synthetic video and a local receiver")
        args.duration = max(args.duration, 19)
    try:
        validate_mode(args.width, args.height, args.fps)
    except ValueError as error:
        parser.error(str(error))
    if not 100 <= args.bitrate <= 20000:
        parser.error("--bitrate must be between 100 and 20000 kbit/s")
    if args.quality is not None and not 10 <= args.quality <= 35:
        parser.error("--quality must be between 10 and 35")
    if args.capture_fps is not None and not args.fps <= args.capture_fps <= 60:
        parser.error("--capture-fps must be between --fps and 60")
    if not math.isfinite(args.duration) or args.duration <= 0:
        parser.error("--duration must be a finite positive number")
    if args.disconnect_after < 0:
        parser.error("--disconnect-after must be nonnegative")
    Gio, GLib, Gst = load_gi()
    before = monitor_names(Gio) if args.virtual_screen else None
    decoder = Gst.parse_launch(
        "appsrc name=input format=time is-live=true "
        "caps=video/x-h264,stream-format=byte-stream,alignment=au "
        "! avdec_h264 ! fakesink name=output sync=false signal-handoffs=true")
    decoded, decode_errors = [], []

    def on_decoded(_sink, buffer, pad):
        caps = pad.get_current_caps().get_structure(0)
        decoded.append((caps.get_value("width"), caps.get_value("height")))
        if buffer.has_flags(Gst.BufferFlags.CORRUPTED):
            decode_errors.append(f"Decoder marked frame {len(decoded)} as corrupted")

    decoder.get_by_name("output").connect("handoff", on_decoded)
    decoder.set_state(Gst.State.PLAYING)
    sender, receiver = socket.socketpair()
    sender.settimeout(3)
    receiver.settimeout(10 if args.lock_cycle else 20)
    errors, received, observed = [], [], []
    observed_cursor = []
    keyframes = []

    def consume():
        try:
            greeting = receive_exact(receiver, HELLO.size)
            assert greeting is not None
            assert HELLO.unpack(greeting) == (MAGIC, args.width, args.height, args.fps)
            while True:
                header = receive_exact(receiver, FRAME.size)
                if header is None:
                    break
                length, pts = FRAME.unpack(header)
                assert 0 < length <= MAX_FRAME
                payload = receive_exact(receiver, length)
                assert payload is not None
                if 5 in nal_types(payload):
                    keyframes.append(len(received))
                if not received:
                    assert {7, 8, 5}.issubset(set(nal_types(payload))), "First AU lacks SPS/PPS/IDR"
                    if args.virtual_screen:
                        observed.extend(monitor_names(Gio))
                        observed_cursor.append(stream.cursor_workaround)
                buffer = Gst.Buffer.new_allocate(None, length, None)
                buffer.fill(0, payload)
                buffer.pts = pts * 1000
                buffer.dts = buffer.pts
                result = decoder.get_by_name("input").emit("push-buffer", buffer)
                assert result == Gst.FlowReturn.OK, result
                if received:
                    assert pts >= received[-1]
                received.append(pts)
                if args.disconnect_after and len(received) >= args.disconnect_after:
                    break
            decoder.get_by_name("input").emit("end-of-stream")
        except BaseException as error:
            errors.append(error)
        finally:
            receiver.close()

    consumer = threading.Thread(target=consume, daemon=True)
    consumer.start()
    try:
        if args.lock_cycle:
            from types import SimpleNamespace

            class SyntheticLockStream(Stream):
                def create_monitor(self):
                    self.screen_lock = SimpleNamespace(active=False,
                        refresh=lambda: self.screen_lock.active, close=lambda: None)
                    self.begin_desktop()

                def begin_desktop(self):
                    self.desktop_first_frame = self.frames
                    self.cursor_ready_reported = True
                    self.start_pipeline()

                def find_connector(self):
                    return None

            stream_class = SyntheticLockStream
        else:
            stream_class = Stream
        stream = stream_class(sender, args.width, args.height, args.fps, args.bitrate,
                              test_pattern=not (args.virtual_screen or args.lock_cycle),
                              duration=args.duration, cursor_workaround=not args.lock_cycle,
                              quality=args.quality, capture_fps=args.capture_fps)
        if args.lock_cycle:
            def lock_state(active):
                stream.screen_lock.active = active
                stream.on_lock_changed(active)
                return False

            stream.timers.append(GLib.timeout_add(1000, stream.capture_failed,
                                                  "Simulated Mutter Closed before ActiveChanged"))
            for delay, active in ((1400, True), (13000, False), (15000, True), (16200, False)):
                stream.timers.append(GLib.timeout_add(delay, lock_state, active))
        try:
            stream.run()
        except RuntimeError:
            if not args.disconnect_after or len(received) < args.disconnect_after:
                raise
        consumer.join(10)
        assert not consumer.is_alive(), "Receiver stuck"
        if errors:
            raise errors[0]
        message = decoder.get_bus().timed_pop_filtered(5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        assert message is not None, "Decoder did not finish"
        if message.type == Gst.MessageType.ERROR:
            raise RuntimeError(message.parse_error())
        assert len(received) > 0 and len(decoded) == len(received), (len(received), len(decoded))
        assert not decode_errors, decode_errors[:3]
        assert all(size == (args.width, args.height) for size in decoded), decoded[:3]
        if args.lock_cycle:
            assert stream.phase == "desktop", stream.phase
            assert len(keyframes) >= 5, "Missing independently decodable encoder segments"
        if args.virtual_screen:
            if not args.disconnect_after:
                assert observed_cursor and observed_cursor[0] is not None, "Cursor workaround was not created"
                assert observed_cursor[0].frames > 0, "Cursor consumer received no frames"
            if observed_cursor and observed_cursor[0] is not None:
                assert observed_cursor[0].closed, "Cursor workaround was not closed"
                assert observed_cursor[0].session is None, "Cursor session was not released"
            assert set(observed) - set(before), ("Virtual monitor not observed", before, observed)
            # Stop replies before Mutter's asynchronous monitor reconfiguration.
            deadline = time.monotonic() + 3
            while monitor_names(Gio) != before and time.monotonic() < deadline:
                time.sleep(0.05)
            assert monitor_names(Gio) == before, "Virtual monitor was not removed"
        rate_control = f"CQP {args.quality}" if args.quality is not None else f"{args.bitrate} kbit/s"
        print(f"PASS: {len(received)} AU received, {len(decoded)} frames decoded at "
              f"{args.width}×{args.height}, {args.fps} fps, {rate_control}; "
              f"source={'GNOME virtual monitor' if args.virtual_screen else 'test pattern'}")
    finally:
        sender.close()
        decoder.set_state(Gst.State.NULL)


if __name__ == "__main__":
    main()
