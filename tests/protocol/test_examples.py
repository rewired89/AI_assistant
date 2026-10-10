import json
from pathlib import Path
import pytest
from protocol import validate_payload, command_types, event_types

EX = sorted((Path(__file__).parents[2] / "protocol" / "examples").glob("*.json"))


@pytest.mark.parametrize("path", EX, ids=lambda p: p.name)
def test_example_valid(path):
    msg = json.loads(path.read_text())
    validate_payload(msg, "command" if msg["type"] in command_types() else "event")


def test_every_spec_message_type_exists():
    for t in ["animation.play","animation.stop","display.set_expression","display.set_brightness","audio.play",
              "audio.start_capture","audio.stop_capture","power.sleep","power.wake","device.get_status","device.set_config"]:
        assert t in command_types()
    for t in ["device.ready","button.pressed","touch.detected","battery.updated","audio.capture.started",
              "audio.capture.chunk","animation.finished","power.sleeping","power.waking","power.woke","command.failed"]:
        assert t in event_types()
