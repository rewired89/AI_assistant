import random
import pytest
from protocol import audio as a


def test_roundtrip_and_end_flag():
    raw = a.pack(a.KIND_CAPTURE, 7, 3, b"\x01\x02\x03\x04")
    f = a.unpack(raw)
    assert (f.kind, f.stream_no, f.seq, f.payload, f.end) == (a.KIND_CAPTURE, 7, 3, b"\x01\x02\x03\x04", False)
    end = a.unpack(a.pack(a.KIND_PLAYBACK, 65535, 0xFFFFFFFF, b"", end=True))
    assert end.end and end.payload == b"" and end.stream_no == 65535 and end.seq == 0xFFFFFFFF
    assert len(raw) == a.HEADER_SIZE + 4 == 16


def good(**kw):
    d = dict(kind=1, stream_no=1, seq=0, payload=b"\x00\x00", end=False, codec=0)
    d.update(kw)
    return a.pack(**d)


@pytest.mark.parametrize("mutate", [
    lambda r: r[:5],                                    # short
    lambda r: b"\x02" + r[1:],                          # version
    lambda r: r[:1] + b"\x00" + r[2:],                  # kind 0
    lambda r: r[:1] + b"\x03" + r[2:],                  # kind 3
    lambda r: r[:2] + b"\x00\x00" + r[4:],              # stream 0
    lambda r: r[:8] + b"\x02" + r[9:],                  # reserved flag
    lambda r: r[:9] + b"\x07" + r[10:],                 # unknown codec
    lambda r: r + b"\x00",                              # trailing byte
    lambda r: r[:-1],                                   # truncated payload
    lambda r: r[:10] + b"\x00\x04" + r[12:],            # declared length != actual
    lambda r: r[:10] + b"\xff\xff" + r[12:],            # declared huge
])
def test_malformed_frames_rejected(mutate):
    with pytest.raises(a.AudioFrameError):
        a.unpack(mutate(good()))


def test_odd_pcm_and_oversize_rejected():
    with pytest.raises(a.AudioFrameError):
        a.unpack(good(payload=b"\x00\x00\x00"))
    a.unpack(good(payload=b"\x00" * 3, codec=1))        # opus payloads are opaque (still unsupported by firmware)
    with pytest.raises(a.AudioFrameError):
        a.pack(1, 1, 0, b"\x00" * (a.MAX_PAYLOAD + 2))


@pytest.mark.parametrize("kw", [dict(kind=9), dict(stream_no=0), dict(stream_no=70000), dict(seq=-1), dict(seq=2 ** 32)])
def test_pack_validates(kw):
    with pytest.raises(a.AudioFrameError):
        good(**kw)


def test_fuzz_never_raises_anything_else():
    rng = random.Random(1234)
    for _ in range(3000):
        n = rng.choice([0, 1, 11, 12, 13, 20, 64, 5000])
        blob = bytes(rng.getrandbits(8) for _ in range(n))
        if n >= 12 and rng.random() < 0.7:
            blob = bytes([1, rng.choice([1, 2]), 0, 1]) + blob[4:]
        try:
            a.unpack(blob)
        except a.AudioFrameError:
            pass


def test_durations():
    assert a.pcm_seconds(32000) == 1.0 and a.CHUNK_BYTES == 1920 and a.pcm_seconds(a.CHUNK_BYTES) == 0.06
