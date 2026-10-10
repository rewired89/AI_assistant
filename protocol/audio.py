"""Binary audio frame format (WebSocket binary messages). Control stays in signed JSON.

    offset size  field
    0      1     version (1)
    1      1     kind: 1 = capture (device -> host), 2 = playback (host -> device)
    2      2     stream_no (uint16, chosen by the host in audio.start_capture / audio.play)
    4      4     seq (uint32, starts at 0, must increase by exactly 1)
    8      1     flags: bit0 = END (last frame of the stream, payload may be empty)
    9      1     codec: 0 = pcm16 16 kHz mono little-endian, 1 = opus (reserved, unsupported)
    10     2     payload_len (uint16, must equal bytes that follow)
    12     n     payload (<= 4096 bytes; PCM payloads must hold whole 16-bit samples)

Frames carry no MAC: they are only accepted on the TLS session that already passed the signed
first-frame gate, and only for a stream the host opened with a signed control command (see docs/audio.md).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

VERSION = 1
KIND_CAPTURE, KIND_PLAYBACK = 1, 2
CODEC_PCM16, CODEC_OPUS = 0, 1
FLAG_END = 0x01
HEADER = struct.Struct(">BBHIBBH")
HEADER_SIZE = HEADER.size  # 12
MAX_PAYLOAD = 4096
SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2
CHUNK_BYTES = 1920  # 60 ms of PCM16 16 kHz mono


class AudioFrameError(ValueError):
    pass


@dataclass(frozen=True)
class AudioFrame:
    kind: int
    stream_no: int
    seq: int
    flags: int
    codec: int
    payload: bytes

    @property
    def end(self) -> bool:
        return bool(self.flags & FLAG_END)


def pack(kind: int, stream_no: int, seq: int, payload: bytes = b"", end: bool = False,
         codec: int = CODEC_PCM16) -> bytes:
    if kind not in (KIND_CAPTURE, KIND_PLAYBACK):
        raise AudioFrameError("bad kind")
    if not 1 <= stream_no <= 0xFFFF:
        raise AudioFrameError("stream_no out of range")
    if not 0 <= seq <= 0xFFFFFFFF:
        raise AudioFrameError("seq out of range")
    if len(payload) > MAX_PAYLOAD:
        raise AudioFrameError("payload too large")
    return HEADER.pack(VERSION, kind, stream_no, seq, FLAG_END if end else 0, codec, len(payload)) + payload


def unpack(frame: bytes) -> AudioFrame:
    """Strict parse. Raises AudioFrameError for anything malformed; callers drop such frames."""
    if len(frame) < HEADER_SIZE:
        raise AudioFrameError("short frame")
    ver, kind, stream_no, seq, flags, codec, plen = HEADER.unpack_from(frame)
    if ver != VERSION:
        raise AudioFrameError(f"unsupported frame version {ver}")
    if kind not in (KIND_CAPTURE, KIND_PLAYBACK):
        raise AudioFrameError("bad kind")
    if stream_no == 0:
        raise AudioFrameError("stream_no 0 is invalid")
    if flags & ~FLAG_END:
        raise AudioFrameError("reserved flag bits set")
    if codec not in (CODEC_PCM16, CODEC_OPUS):
        raise AudioFrameError("unknown codec")
    if plen > MAX_PAYLOAD or len(frame) - HEADER_SIZE != plen:
        raise AudioFrameError("length mismatch")
    payload = bytes(frame[HEADER_SIZE:])
    if codec == CODEC_PCM16 and plen % 2:
        raise AudioFrameError("odd PCM payload length")
    return AudioFrame(kind, stream_no, seq, flags, codec, payload)


def pcm_seconds(n_bytes: int) -> float:
    return n_bytes / BYTES_PER_SECOND
