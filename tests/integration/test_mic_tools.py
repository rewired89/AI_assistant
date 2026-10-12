import sys

from companion.speech.fakes import tone
from tools import mic_check
from tools.desk_robot import ArecordMic


def fake(code: str, **kw):
    return ArecordMic(argv=[sys.executable, "-u", "-c", code], silence_after_s=0.6, **kw)


EMIT = "import sys,time\nwhile True:\n    sys.stdout.buffer.write(DATA); sys.stdout.buffer.flush(); time.sleep(0.05)\n"


def emit(data: bytes) -> str:
    return f"DATA={data!r}\n" + EMIT


def run(mic, seconds=1.2):
    lines = []
    try:
        return mic_check.check(mic, seconds, out=lines.append), lines
    finally:
        mic.close()


def test_a_working_microphone_is_reported_ok_with_a_level_bar():
    r, lines = run(fake(emit(tone(0.1, 300, 9000))))
    assert r["verdict"].startswith("OK") and r["status"] == "ok" and lines and "#" in lines[0]


def test_a_muted_microphone_that_sends_zeros_is_called_silent():
    r, _ = run(fake(emit(bytes(3200))))
    assert r["verdict"].startswith("SILENT") and "muted" in r["verdict"]


def test_a_very_quiet_and_a_clipping_microphone_are_called_out():
    assert run(fake(emit(tone(0.1, 300, 150))))[0]["verdict"].startswith("VERY QUIET")
    assert run(fake(emit(tone(0.1, 300, 32000))))[0]["verdict"].startswith("TOO LOUD")


def test_a_recorder_that_dies_reports_its_own_error_message_instead_of_failing_silently():
    code = "import sys; sys.stderr.write('arecord: main:831: audio open error: Device or resource busy\\n'); sys.exit(1)"
    r, _ = run(fake(code), seconds=1.5)
    assert r["verdict"].startswith("NO AUDIO") and "Device or resource busy" in r["verdict"]


def test_a_recorder_that_hangs_without_data_is_reported():
    r, _ = run(fake("import time; time.sleep(30)"), seconds=1.2)
    assert r["verdict"].startswith("NO AUDIO") and "no audio from the microphone" in r["verdict"]


def test_a_missing_program_says_how_to_install_it():
    m = ArecordMic(argv=["definitely-not-arecord-xyz"])
    assert m.read() == b"" and "alsa-utils" in m.status()
    m.close()


def test_the_dying_recorder_is_restarted_once(tmp_path):
    marker = tmp_path / "ran"
    code = (f"import os,sys\nm={str(marker)!r}\nif not os.path.exists(m):\n    open(m,'w').close(); sys.exit(1)\n"
            "sys.stdout.buffer.write(bytes(3200)); sys.stdout.buffer.flush()\nimport time; time.sleep(5)")
    mic = fake(code)
    got = b""
    for _ in range(40):
        got = mic.read()
        if got:
            break
    mic.close()
    assert got and mic.restarts == 1


def test_dc_offset_is_not_loudness():
    import struct
    from companion.speech.vad import rms
    assert rms(struct.pack("<320h", *([9000] * 320))) < 1


def test_one_loud_click_is_not_too_loud():
    import struct
    from tools.mic_check import check
    quiet, click = struct.pack("<320h", *([40, -40] * 160)), struct.pack("<320h", *([20000, -20000] * 160))

    class M:
        def __init__(self):
            self.n = 0
        def read(self):
            self.n += 1
            return click if self.n == 1 else quiet
        def status(self):
            return "ok"
    r = check(M(), seconds=0.3, out=lambda *_: None)
    assert not r["verdict"].startswith("TOO LOUD")


def test_stats_reports_offset_and_clipping():
    import struct
    from tools.mic_check import stats
    s = stats(struct.pack("<3200h", *([32767] * 3200)))
    assert s["clipped_pct"] == 100 and s["dc_offset"] == 32767 and s["median_level"] == 0
