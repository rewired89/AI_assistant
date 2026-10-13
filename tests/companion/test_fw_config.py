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
