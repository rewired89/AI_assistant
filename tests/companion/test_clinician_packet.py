from companion.psychology.exercises.library import load_library
from tools import review_packet as rp


def test_committed_packet_matches_what_the_code_would_say():
    files = rp.render()
    for name, text in files.items():
        assert (rp.OUT / name).read_text() == text, f"{name} is out of date: run python -m tools.review_packet"


def test_every_exercise_step_and_safety_reply_is_in_the_packet():
    d = rp.build()
    ids = {i for i, _, _ in d.items}
    for level in ("crisis", "abuse", "violence", "support"):
        assert f"S-{level}" in ids
    for eid, ex in load_library().items():
        if ex["kind"] != "conflict_guide":
            for st in ex["steps"] or []:
                if st.get("text") or st.get("prompt"):
                    assert f"X-{eid}.{st['id']}" in ids
    assert len(ids) == len(d.items), "review ids must be unique"
    assert all(text.strip() for _, _, text in d.items)
