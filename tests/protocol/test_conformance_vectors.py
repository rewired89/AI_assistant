"""The committed firmware conformance vectors must (a) be current and (b) replay exactly against the simulator."""
import json

import pytest

from protocol.vectors_lib import DIR, replay
from tools import gen_vectors

FILES = sorted(DIR.glob("*.json"))


def test_vectors_are_current_with_the_simulator():
    assert gen_vectors.main(["--check"]) == 0, "run: python -m tools.gen_vectors and review the diff"


def test_every_scenario_has_a_file_and_vice_versa():
    assert {b.name + ".json" for b in gen_vectors.scenarios()} == {f.name for f in FILES}


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_replay_matches_recorded_expectations(path, tmp_path):
    v = json.loads(path.read_text())
    boot, results = replay(v, tmp_path)
    assert boot == v["boot_expect"]
    for i, (step, got) in enumerate(zip(v["steps"], results)):
        assert got == step["expect"], f"step {i}: {step.get('note')}"


def test_vectors_contain_no_secret_other_than_the_documented_test_key():
    text = "".join(f.read_text() for f in FILES)
    assert text.count("00112233445566778899aabbccddeeff") >= len(FILES)       # the published test key, nothing else
