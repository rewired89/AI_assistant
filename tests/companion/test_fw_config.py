"""tools/fw_config.py, plus a guard that the firmware's hand-built signed message matches what the host verifies."""
import hashlib
import hmac
import json

import pytest

from protocol.auth import Authenticator, _signing_input
from tools.fw_config import apply


def test_bundle_values_land_in_sdkconfig_once(tmp_path):
    (tmp_path / "sdkconfig").write_text('CONFIG_X=1\n# CONFIG_NT_DEVICE_ID is not set\n')
    b = tmp_path / "b.json"
    b.write_text(json.dumps({"device_id": "bot-01", "key": "ab" * 32}))
    apply(b, tmp_path)
    apply(b, tmp_path)
    text = (tmp_path / "sdkconfig").read_text()
    assert text.count("CONFIG_NT_DEVICE_KEY_HEX") == 1 and 'CONFIG_NT_DEVICE_ID="bot-01"' in text and "CONFIG_X=1" in text


@pytest.mark.parametrize("bundle", [{"device_id": "bot-01", "key": "zz" * 32}, {"device_id": "x", "key": "ab" * 32}, {"key": "ab" * 32}])
def test_bad_bundles_are_refused(tmp_path, bundle):
    (tmp_path / "sdkconfig").write_text("")
    b = tmp_path / "b.json"
    b.write_text(json.dumps(bundle))
    with pytest.raises(ValueError):
        apply(b, tmp_path)


def test_firmware_signed_hello_layout_is_accepted_by_the_host():
    """Same string layout as firmware/net_test/main/main.c send_hello(). If the schema or canonical form changes, this fails and the firmware must follow."""
    key, nonce, ts, did, mid = bytes.fromhex("ab" * 32), "0123456789abcdef01234567", 1800000000, "bot-01", "hello12345678"
    head = '{"auth":{"kid":1,%s"nonce":"%s","ts":%d},"device_id":"%s","message_id":"%s",'
    tail = ('"payload":{"boot_cause":"power_on","firmware_version":"nt-net-0.1","protocol_versions":[1],"recovered_state":false},'
            '"protocol_version":1,"type":"device.ready"}')
    body = (head % ("", nonce, ts, did, mid)) + tail
    mac = hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
    msg = json.loads((head % ('"mac":"%s",' % mac, nonce, ts, did, mid)) + tail)
    assert _signing_input(msg).decode() == body
    assert Authenticator(key, lambda: ts, did).verify(msg) == 1


def test_firmware_verifies_host_messages_by_removing_the_mac_member():
    """firmware/net_test verify_incoming(): the signed bytes are the host's encoded text with the `"mac":"<64 hex>",` member removed."""
    from protocol import build, encode
    a = Authenticator(bytes.fromhex("ab" * 32), lambda: 1800000000, "bot-01")
    raw = encode(a.sign(build("animation.play", {"name": "calm", "loop": True, "priority": 2}))).decode()
    i = raw.index('"mac":"')
    assert raw[i + 71:i + 73] == '",'
    assert (raw[:i] + raw[i + 73:]).encode() == _signing_input(json.loads(raw))


def test_firmware_command_failed_layout_is_accepted_by_the_host():
    key, nonce, ts, did, mid = bytes.fromhex("ab" * 32), "0123456789abcdef01234567", 1800000000, "bot-01", "fail12345678"
    payload = '{"code":"unsupported_command","command_id":"abcdefgh12","message":"not implemented in this test firmware"}'
    head = '{"auth":{"kid":1,%s"nonce":"%s","ts":%d},"device_id":"%s","message_id":"%s","payload":'
    tail = ',"protocol_version":1,"type":"command.failed"}'
    body = (head % ("", nonce, ts, did, mid)) + payload + tail
    mac = hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
    msg = json.loads((head % ('"mac":"%s",' % mac, nonce, ts, did, mid)) + payload + tail)
    assert _signing_input(msg).decode() == body
    assert Authenticator(key, lambda: ts, did).verify(msg) == 1
    from protocol import validate_payload
    validate_payload(msg, "event")


def test_voice_firmware_event_payloads_validate_against_the_schema():
    """The exact payload strings built in firmware/voice_test/main/main.c (keys already sorted) must be accepted by the host's schema."""
    from protocol import validate_payload
    cases = {
        "audio.playback.finished": '{"bytes_played":%u,"result":"%s","stream_id":"%s","underruns":%d}' % (96000, "completed", "s75c8f823f7", 0),
        "audio.capture.started": '{"capture_id":"%s"}' % "cap-1",
        "audio.capture.stopped": '{"capture_id":"%s","reason":"%s"}' % ("cap-1", "host_request"),
        "command.accepted": '{"command_id":"%s","command_type":"%s"}' % ("abcdefgh12", "audio.play"),
        "command.completed": '{"command_id":"%s"}' % "abcdefgh12",
        "command.failed": '{"code":"%s","command_id":"%s","message":"%s"}' % ("not_allowed", "abcdefgh12", "microphone is muted"),
    }
    for mtype, raw in cases.items():
        payload = json.loads(raw)
        assert list(payload) == sorted(payload), f"{mtype}: keys must be written in sorted order for canonical signing"
        validate_payload({"type": mtype, "payload": payload, "message_id": "abcdefgh12"}, "event")


def test_voice_firmware_capture_frame_header_matches_the_host_parser():
    """Header bytes laid out like capture_task(): version 1, kind 1, stream_no, seq, flags, codec 0, length."""
    from protocol import audio as aud
    frame = bytes([1, 1, 0x12, 0x34, 0, 0, 0, 7, 0, 0, 0x07, 0x80]) + bytes(1920)
    f = aud.unpack(frame)
    assert (f.kind, f.stream_no, f.seq, f.flags, f.codec, len(f.payload)) == (aud.KIND_CAPTURE, 0x1234, 7, 0, aud.CODEC_PCM16, 1920)
    end = aud.unpack(bytes([1, 1, 0x12, 0x34, 0, 0, 0, 8, 1, 0, 0, 0]))
    assert end.end and end.payload == b""


def test_deploy_set_stt_changes_only_the_stt_block(tmp_path):
    import yaml
    from tools.deploy import main, set_stt
    cfg = tmp_path / "companion.yaml"
    cfg.write_text(yaml.safe_dump({"species": "ninetales", "stt": {"engine": "none"}, "tts": {"engine": "espeak"}}))
    model = tmp_path / "m"
    model.mkdir()
    (model / "model.bin").write_bytes(b"x")
    assert main(["set-stt", "--config", str(cfg), "--model", str(model)]) == 0
    out = yaml.safe_load(cfg.read_text())
    assert out["stt"] == {"engine": "whisper", "model": str(model.resolve())} and out["tts"] == {"engine": "espeak"} and out["species"] == "ninetales"
    with pytest.raises(ValueError):
        set_stt(cfg, tmp_path / "missing")


def test_deploy_set_tts_switches_to_kokoro_and_touches_nothing_else(tmp_path):
    import yaml
    from tools.deploy import main
    cfg = tmp_path / "companion.yaml"
    cfg.write_text(yaml.safe_dump({"species": "ninetales", "stt": {"engine": "none"}, "tts": {"engine": "espeak"}}))
    model = tmp_path / "k"
    model.mkdir()
    (model / "kokoro-v1.0.onnx").write_bytes(b"x")
    (model / "voices-v1.0.bin").write_bytes(b"x")
    assert main(["set-tts", "--config", str(cfg), "--model", str(model), "--voice", "af_sarah"]) == 0
    out = yaml.safe_load(cfg.read_text())
    assert out["tts"] == {"engine": "kokoro", "model": str(model.resolve()), "voice": "af_sarah"} and out["stt"] == {"engine": "none"}
    empty = tmp_path / "e"
    empty.mkdir()
    assert main(["set-tts", "--config", str(cfg), "--model", str(empty)]) == 1


def test_sprite_bundle_roundtrips_and_fits_the_partition():
    from tools import sprite_bundle
    data = sprite_bundle.build(sprite_bundle.ROOT / "assets" / "sprites")
    assert len(data) <= sprite_bundle.PARTITION_SIZE
    names = [line.split(":")[0] for line in sprite_bundle.check(data)]
    assert names == ["baby", "adult"]


def test_service_unit_runs_from_the_repo_with_the_venv_python(tmp_path):
    from tools import deploy
    cfg = tmp_path / "companion.yaml"
    cfg.write_text("species: ninetales\n")
    unit = deploy.service_unit(cfg, "/repo/.venv/bin/python", "rewired89", tmp_path)
    assert f"WorkingDirectory={tmp_path}" in unit and f"ExecStart=/repo/.venv/bin/python -m companion --config {cfg}" in unit
    assert "User=rewired89" in unit and "Restart=always" in unit
    import pytest
    with pytest.raises(ValueError):
        deploy.service_unit(tmp_path / "nope.yaml", "python", "u", tmp_path)


def test_service_debug_flag_adds_the_logging_flags_only_when_asked(tmp_path):
    from tools import deploy
    cfg = tmp_path / "companion.yaml"
    cfg.write_text("species: ninetales\n")
    assert "--show-heard" not in deploy.service_unit(cfg, "python", "u", tmp_path)
    assert "--console --show-heard" in deploy.service_unit(cfg, "python", "u", tmp_path, debug=True)


def test_set_voice_changes_only_the_voice_block(tmp_path):
    import yaml
    import pytest
    from tools import deploy
    cfg = tmp_path / "c.yaml"
    cfg.write_text(yaml.safe_dump({"species": "ninetales", "llm": {"engine": "ollama"}}))
    out = deploy.set_voice(cfg, 6, "hey girl")
    assert out == {"follow_up_s": 6, "wake_phrase": "hey girl"}
    assert yaml.safe_load(cfg.read_text())["llm"] == {"engine": "ollama"}
    deploy.set_voice(cfg, None, "")
    assert yaml.safe_load(cfg.read_text())["voice"] == {"follow_up_s": 6, "wake_phrase": ""}
    with pytest.raises(ValueError):
        deploy.set_voice(cfg, 99, None)
