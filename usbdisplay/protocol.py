"""UTD1 framing. All integers are unsigned and big endian."""

import struct

MAGIC = b"UTD1"
HELLO = struct.Struct("!4sIII")
FRAME = struct.Struct("!IQ")
MAX_FRAME = 8 * 1024 * 1024


def validate_mode(width, height, fps):
    if any(n < 160 or n > 2560 or n % 2 for n in (width, height)):
        raise ValueError("Ширина и высота должны быть чётными числами от 160 до 2560")
    if not 5 <= fps <= 60:
        raise ValueError("Частота кадров должна быть от 5 до 60")


def hello(width, height, fps):
    validate_mode(width, height, fps)
    return HELLO.pack(MAGIC, width, height, fps)


def frame_header(length, pts_us):
    if not 0 < length <= MAX_FRAME:
        raise ValueError("Недопустимый размер видеокадра")
    if not 0 <= pts_us < 2**63:
        raise ValueError("Недопустимая метка времени")
    return FRAME.pack(length, pts_us)
