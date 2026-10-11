import copy

import pytest
import yaml

from companion.psychology.conflict_navigation import build_conflict_exercise, build_revisit_exercise
from companion.psychology.exercises import LibraryError, load_library, validate_exercise
from companion.psychology.safety.validator import violations
from companion.psychology.sources import SourceError, load_sources

SPEC_EXERCISES = ["naming_emotions", "facts_vs_interpretations", "reflective_listening_practice", "conflict_map",
                  "prepare_difficult_conversation", "values_clarification", "self_compassion_break", "break_and_return_plan",
                  "goal_review", "repair_attempt_reflection"]


@pytest.fixture(scope="module")
def sources():
    return load_sources()


@pytest.fixture(scope="module")
def lib(sources):
    return load_library(sources=sources)


def test_every_exercise_the_spec_names_exists(lib):
    assert set(SPEC_EXERCISES) <= set(lib)


def test_every_exercise_has_all_required_fields(lib):
    required = ("exercise_id", "name", "intended_skill", "source_or_evidence_basis", "when_to_offer", "when_not_to_offer",
                "estimated_duration", "instructions", "reflection_questions", "privacy_behavior", "success_criteria", "limitations")
    for eid, ex in lib.items():
        assert all(k in ex for k in required), eid
        assert ex["limitations"] and ex["privacy_behavior"].strip() and ex["success_criteria"]


def test_every_source_is_registered_with_limits_and_risks(sources, lib):
    used = {s for ex in lib.values() for s in ex["source_or_evidence_basis"]}
    assert used <= set(sources)
    for sid, s in sources.items():
        assert s["limits"].strip() and s["risks"].strip(), sid
        assert s["evidence_level"] in ("strong", "moderate", "limited", "mixed", "unestablished")


def test_nothing_is_rated_stronger_than_its_checking(sources):
    for sid, s in sources.items():
        if s["checked"] == "not checked":
            assert s["evidence_level"] in ("limited", "mixed", "unestablished", "moderate"), sid
            assert "verify" in (s["limits"] + s["summary"]).lower() or s["evidence_level"] != "moderate" or sid in ("reflective_listening",), sid


def test_popular_but_weak_techniques_are_labeled_weak(sources):
    assert sources["grounding_54321"]["evidence_level"] == "unestablished"
    assert sources["nvc"]["evidence_level"] == "limited"
    assert sources["gottman_flooding_break"]["evidence_level"] == "limited"
    assert "rule of thumb" in sources["gottman_flooding_break"]["limits"]


def test_source_validation_rejects_bad_entries(tmp_path):
    base = yaml.safe_load(open("companion/psychology/sources/sources.yaml"))
    bad = copy.deepcopy(base)
    bad["sources"]["affect_labeling"]["limits"] = ""
    p = tmp_path / "s.yaml"; p.write_text(yaml.safe_dump(bad))
    with pytest.raises(SourceError):
        load_sources(p)
    bad = copy.deepcopy(base)
    bad["sources"]["affect_labeling"]["evidence_level"] = "proven"
    p.write_text(yaml.safe_dump(bad))
    with pytest.raises(SourceError):
        load_sources(p)
    bad = copy.deepcopy(base)
    bad["sources"]["self_compassion"]["urls"] = []
    p.write_text(yaml.safe_dump(bad))
    with pytest.raises(SourceError):
        load_sources(p)


def test_library_validation_rejects_bad_exercises(lib, sources):
    ok = copy.deepcopy(lib["naming_emotions"])
    validate_exercise(ok, sources)
    mutations = {
        "missing field": lambda e: e.pop("limitations"),
        "unknown source": lambda e: e.__setitem__("source_or_evidence_basis", ["made_up"]),
        "no safety clause": lambda e: e.__setitem__("when_not_to_offer", ["She is tired"]),
        "success = agreement": lambda e: e.__setitem__("success_criteria", ["She agrees with the robot"]),
        "empty limitations": lambda e: e.__setitem__("limitations", []),
        "duplicate step": lambda e: e["steps"].append(copy.deepcopy(e["steps"][0])),
        "bad step kind": lambda e: e["steps"].append({"id": "x", "kind": "execute", "prompt": "p"}),
        "choice without options": lambda e: e["steps"].append({"id": "y", "kind": "ask_choice", "prompt": "p"}),
        "template from the future": lambda e: e["steps"].insert(0, {"id": "t", "kind": "build_text", "template": "{shift}"}),
    }
    for name, mutate in mutations.items():
        bad = copy.deepcopy(ok)
        mutate(bad)
        with pytest.raises(LibraryError):
            validate_exercise(bad, sources)


def all_robot_text(ex):
    out = [ex["name"], ex["instructions"], *ex["reflection_questions"]]
    for st in ex["steps"]:
        out += [st.get("text", ""), st.get("prompt", ""), st.get("lead", ""), st.get("route_text", "")]
    return [t for t in out if t]


def test_all_scripted_text_passes_the_psychology_output_filters(lib):
    exercises = list(lib.values()) + [build_conflict_exercise(lib["conflict_navigation"], d) for d in ("quick", "full")] \
        + [build_revisit_exercise(lib["conflict_navigation"])]
    for ex in exercises:
        for text in all_robot_text(ex):
            assert violations(text) == [], (ex["exercise_id"], text)


def test_no_scripted_text_labels_people_or_diagnoses(lib):
    banned = ["attachment style", "avoidant", "anxious type", "narcissis", "toxic", "gaslight", "disorder", "diagnos", "your fault",
              "you're wrong", "you are wrong", "should leave", "break up with", "you must", "you have to"]
    for ex in lib.values():
        for text in all_robot_text(ex):
            low = text.lower()
            assert not any(b in low for b in banned), (ex["exercise_id"], text)


def test_conflict_stage_order_follows_the_spec(lib):
    steps = [s["id"] for s in build_conflict_exercise(lib["conflict_navigation"], "full")["steps"]]
    order = ["notice", "emotion", "pattern", "their_side", "facts", "assumptions", "need", "boundary", "choose", "next_step"]
    positions = [steps.index(s) for s in order]
    assert positions == sorted(positions)
    quick = [s["id"] for s in build_conflict_exercise(lib["conflict_navigation"], "quick")["steps"]]
    assert "pattern" not in quick and {"notice", "emotion", "need", "choose", "next_step"} <= set(quick)


def test_the_perspective_questions_from_the_spec_are_all_present(lib):
    texts = " ".join(all_robot_text(build_conflict_exercise(lib["conflict_navigation"], "full"))).lower() + " " + \
            " ".join(all_robot_text(lib["break_and_return_plan"])).lower()
    for q in ["what part of this conversation became overwhelming", "what would help you feel safe enough to return to the topic",
              "what makes it difficult to leave a problem unresolved until later", "specific time",
              "what would make a break feel respectful to both people", "one thing each person could do differently next time"]:
        assert q in texts, q


def test_break_plan_teaches_both_directions_and_a_return_time(lib):
    text = " ".join(all_robot_text(lib["break_and_return_plan"])).lower()
    for must in ["taking space is healthy", "pushing someone who asked for space", "specific time", "i'm overwhelmed and need some time",
                 "i'm not willing to have this disappear", "quietly turns into silence", "reminder will not say what it's about"]:
        assert must in text, must
    assert "abuse" in " ".join(lib["break_and_return_plan"]["when_not_to_offer"]).lower()


def test_exercises_that_could_trigger_carry_cautions(lib):
    assert any("trauma" in w.lower() for w in lib["grounding_54321"]["when_not_to_offer"])
    assert any("dizz" in w.lower() for w in lib["slow_breathing"]["when_not_to_offer"])
    assert lib["grounding_54321"].get("save_notes") is False
    assert "abuse" in " ".join(lib["facts_vs_interpretations"]["when_not_to_offer"]).lower()


def test_evidence_document_is_in_sync_with_the_register():
    from pathlib import Path
    from tools.evidence_table import render
    assert Path("docs/psychology-evidence.md").read_text() == render() + "\n", "run: python -m tools.evidence_table > docs/psychology-evidence.md"
