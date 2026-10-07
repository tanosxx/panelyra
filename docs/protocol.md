# Video and language protocols

[Home](../README.md) · [Architecture](architecture.md)

UTD1 is a small, unidirectional video protocol over a TCP byte stream. Each
connection starts a new H.264 stream. There is no discovery, negotiation,
authentication, encryption, audio, input or acknowledgement message in UTD1.
USB discovery and connection restrictions are transport responsibilities.
Video uses TCP port 27183. Optional language control uses a separate connection
on port 27184 and does not add any bytes to the UTD1 stream.

## Stream header

All integers are unsigned and big-endian. Read exactly 16 bytes before reading
video packets; TCP reads are not guaranteed to match message boundaries.

| Offset | Size | Field | Allowed value |
| --- | --- | --- | --- |
| 0 | 4 bytes | Magic | ASCII `UTD1` (`55 54 44 31`) |
| 4 | uint32 | Width | Even, 160–2560 |
| 8 | uint32 | Height | Even, 160–2560 |
| 12 | uint32 | FPS | 5–60 |

FPS is the intended transmission limit, not a guarantee of constant-rate packet
arrival. A static desktop can send fewer frames. Width and height refer to the
visible picture; the codec may use padded dimensions with cropping in its SPS.
The limits define valid framing, not the capabilities of every Android decoder.

## Video packets

After the header, repeat:

| Size | Field | Constraint |
| --- | --- | --- |
| uint32 | Payload length | 1–8,388,608 bytes |
| uint64 | Presentation timestamp in microseconds | 0 through 2^63−1; non-decreasing |
| Payload length | H.264 access unit | Annex B byte stream |

The timestamp range fits Android's signed 64-bit representation. The current
sender normalizes the first timestamp to zero. One payload contains one complete
access unit and can contain several NAL units, including multiple slices.
Both three-byte and four-byte Annex B start codes are accepted by the receiver.

The first packet must contain SPS, PPS and an IDR picture. The receiver converts
SPS/PPS to `csd-0`/`csd-1`, removes those configuration NAL units from the first
payload, and preserves AUD, SEI and the IDR slices for decoding. The sender uses
Baseline without B frames, which avoids frame reordering for the current client.

## Ending and changing a stream

Closing TCP ends the stream; there is no explicit end packet. A truncated header
or payload, invalid size/timestamp, timeout or decoder error also terminates that
connection. Changing resolution or transmission rate requires reconnecting with
a new header and decoder configuration.

The sender must not discard arbitrary encoded access units after encoding.
H.264 reference dependencies would make later frames corrupt. Drop excess raw
frames before encoding, or discard frames only after decoding on the receiver.

## Compatibility

The protocol magic remains `UTD1` after the application was renamed Panelyra.
The Android application ID also remains `dev.usbdisplay.client`. A future wire
change that an old client cannot safely interpret needs a new protocol version;
do not append an incompatible field to the existing header.

Implementations and tests:

The source links below are intended for a repository checkout or the GitHub
source view. The installed Linux documentation package does not contain the
Android source tree and test files.

- [Python framing](../usbdisplay/protocol.py)
- [Java parser](../android/app/src/main/java/dev/usbdisplay/client/VideoProtocol.java)
- [Python tests](../tests/test_protocol.py)
- [Java parser tests](../android/tests/dev/usbdisplay/client/VideoProtocolTest.java)

## Language control

The Android foreground session also listens on TCP port **27184**, on the same
concrete USB address or IPv4 loopback address as video. It applies the same peer
filter: the selected USB subnet, or IPv4 loopback for ADB. The sender binds its
control connection to the selected USB address and checks its route, or uses an
owned ADB forwarding rule. This is not an authenticated or encrypted channel.

One connection carries one request, exactly **seven ASCII bytes**:

| Offset | Size | Field | Allowed value |
| --- | --- | --- | --- |
| 0 | 5 bytes | Magic | `PLYL1` |
| 5 | 2 bytes | Language | `en` or `ru` |

The sender writes `PLYL1en` or `PLYL1ru`, then shuts down its write side so the
receiver can verify the end of the request. No newline, length prefix or trailing
bytes are permitted. TCP reads must be assembled until the complete request and
end-of-input are available. Invalid magic/language, a truncated or longer request,
or a timeout must not change the saved language.

After accepting and saving the choice, the receiver echoes the exact seven
request bytes as its acknowledgement and closes the connection. On rejection
it closes without an error payload. An acknowledgement lost after saving leaves
the sender uncertain, so repeating the same setting is safe. An unavailable
control listener or a failed request must not terminate the video connection.

Only `en` and `ru` travel on the wire. Linux resolves `auto` to its own system
language before sending. Android uses its system locale before its first valid
received choice, then persists the last accepted value in private preferences.
Updating visible strings must preserve the existing activity, video surface and
decoder. There is no query or reset-to-system message in this protocol.

The independent port preserves UTD1 compatibility: older Android receivers can
continue displaying video, and senders that omit language control leave the
tablet's saved or system-language choice in effect.
