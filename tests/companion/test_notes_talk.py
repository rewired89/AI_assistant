import pytest

from companion.productivity import ProductivityStore, StoreError
from companion.productivity.talk import NotesTalk, split_items


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def rig(tmp_path):
    c = Clock()
    st = ProductivityStore(tmp_path / "p.db", c)
    t = NotesTalk(st, c, phone_hint=True)

    class R:
        store, talk, clock = st, t, c

        def say(self, *us):
            out = []
            for u in us:
                self.clock.t += 5
                r = t.handle(u)
                out.append(r.text if r else None)
            return out if len(out) > 1 else out[0]
    return R()


def test_one_spoken_note_is_saved_in_her_words_and_confirmed_after_the_save(rig):
    r = rig.say("Remember this idea. I want to try making a little robot enclosure with removable magnetic panels.")
    n = rig.store.notes()[0]
    assert n["body"] == "I want to try making a little robot enclosure with removable magnetic panels." and "idea" in n["tags"]
    assert n["kind"] == "dictated" and n["source"] == "voice" and n["cleaned"] is False
    assert r.startswith("Got it! I've saved your idea") and "review it on your phone" in r


def test_write_this_down_then_the_next_sentence_is_the_note(rig):
    assert rig.say("Nine Tails write this down") == "Okay, go ahead."
    assert rig.store.notes() == []
    r = rig.say("I need to research how to make the sprite animations use less memory")
    assert "saved your note" in r and rig.store.notes()[0]["body"].startswith("I need to research")


def test_a_long_voice_note_over_several_turns_with_pause_and_continue(rig):
    assert "listening" in rig.say("save a voice note")
    rig.say("first thought is about the enclosure")
    assert "Paused" in rig.say("pause")
    assert "paused" in rig.say("something said while paused").lower()
    rig.say("continue")
    rig.say("second thought is about the magnets")
    assert rig.store.notes() == []                                   # nothing is saved until she says so
    r = rig.say("done")
    n = rig.store.notes()[0]
    assert n["kind"] == "voice" and n["body"] == "first thought is about the enclosure second thought is about the magnets"
    assert "something said while paused" not in n["body"] and "saved your note" in r


def test_cancel_throws_the_draft_away_and_says_so(rig):
    rig.say("take a note", "this will be thrown away")
    assert "Nothing was saved" in rig.say("cancel") and rig.store.notes() == []


def test_the_note_is_never_claimed_saved_when_the_store_failed_and_the_draft_survives(rig):
    rig.say("save a voice note", "an important thought")
    orig = rig.store.add_note
    rig.store.add_note = lambda *a, **k: (_ for _ in ()).throw(StoreError("the disk is full"))
    r = rig.say("done")
    assert "couldn't save" in r and "saved your" not in r.lower().replace("isn't saved", "") and rig.talk.dictating
    rig.store.add_note = orig
    r2 = rig.say("done")
    assert "saved your note" in r2 and rig.store.notes()[0]["body"] == "an important thought"


def test_empty_dictation_makes_no_note(rig):
    rig.say("save a voice note")
    assert "nothing to save" in rig.say("done") and rig.store.notes() == []


def test_titles_by_voice_while_dictating_and_right_after(rig):
    rig.say("save a voice note", "call it Enclosure plan", "magnets and panels")
    rig.say("done")
    assert rig.store.notes()[0]["title"] == "Enclosure plan"
    assert "Magnet box" in rig.say("rename my last note to Magnet box")
    assert rig.store.notes()[0]["title"] == "Magnet box"


def test_verbatim_by_default_and_light_cleanup_only_when_she_asks(rig):
    rig.say("write this down: um so i need milk and eggs")
    assert rig.store.notes()[0]["body"] == "um so i need milk and eggs" and rig.store.notes()[0]["cleaned"] is False
    assert "tidy" in rig.say("clean up my dictation")
    rig.say("write this down: um so i need milk and eggs")
    n = rig.store.notes()[0]
    assert n["cleaned"] is True and n["body"] == "So i need milk and eggs."
    assert "word for word" in rig.say("keep my dictation word for word")
    rig.say("write this down: um again")
    assert rig.store.notes()[0]["body"] == "um again"


def test_read_the_last_note_and_search_and_delete_with_confirmation(rig):
    rig.say("write this down: the robot enclosure needs magnetic panels")
    rig.clock.t += 3 * 86400
    rig.say("write this down: buy flour and eggs for the cake")
    assert "buy flour and eggs" in rig.say("read my last note")
    assert "robot enclosure" in rig.say("find the note I made about the robot")
    assert "couldn't find" in rig.say("find the note about quantum physics")
    r = rig.say("delete that note")
    assert "Do you want me to delete" in r and len(rig.store.notes()) == 2                  # asked, not done
    assert "kept it" in rig.say("no")
    assert len(rig.store.notes()) == 2
    rig.say("delete that note")
    assert "deleted" in rig.say("yes").lower() and len(rig.store.notes()) == 1


def test_several_matches_ask_which_one(rig):
    rig.say("write this down: robot idea one about wheels")
    rig.say("write this down: robot idea two about tails")
    r = rig.say("find the note about the robot")
    assert "found 2" in r and "Which one" in r
    assert "robot idea" in rig.say("the second one")


def test_what_did_i_write_down_yesterday(rig):
    rig.say("write this down: an old thought")
    rig.clock.t += 86400
    rig.say("write this down: a newer thought")
    r = rig.say("what did I write down yesterday")
    assert "old thought" in r and "newer" not in r
    assert "didn't write anything" in rig.say("what did I write down last week") or "old thought" in rig.say("what did I write down last week")


def test_delete_without_a_note_in_mind_asks_which(rig):
    assert "Which note" in rig.say("delete that note")


def test_grocery_list_by_voice_then_add_read_and_complete(rig):
    r = rig.say("Make me a grocery list. We need eggs, rice, milk, and fruit.")
    assert r == "Your grocery list is ready! I've added 4 items."
    lst = rig.store.find_list("grocery")[0]
    assert [i["text"] for i in rig.store.get_list(lst["id"])["items"]] == ["eggs", "rice", "milk", "fruit"]
    assert "Added 1 item" in rig.say("add bananas to my grocery list")
    assert "eggs, rice, milk, fruit, bananas" in rig.say("what's on my grocery list")
    assert "Marked eggs as done" in rig.say("mark eggs as complete")
    assert "eggs" not in rig.say("what's on my grocery list")
    assert "Put eggs back" in rig.say("uncheck eggs")


def test_a_task_is_never_completed_just_because_she_talked_about_it(rig):
    rig.say("make me a daily tasks list: exercise, read")
    rig.say("I did my exercise today and it felt great")
    rig.say("I'm going to exercise later")
    assert all(not i["done"] for i in rig.store.get_list(rig.store.lists()[0]["id"])["items"])
    assert rig.say("mark my words, that was a good workout") is None             # ordinary speech is left alone


def test_ambiguity_asks_instead_of_guessing(rig):
    rig.say("make me a grocery list: milk")
    rig.say("make me a robot project list: batteries")
    r = rig.say("add tape to my list")
    assert "Which list" in r and all(i["text"] != "tape" for l in rig.store.lists() for i in rig.store.get_list(l["id"])["items"])
    assert "Added 1 item to your robot project" in rig.say("robot project")
    rig.say("make me a chores list: milk")
    r = rig.say("mark milk as done")
    assert "Which one" in r and all(not i["done"] for l in rig.store.lists() for i in rig.store.get_list(l["id"])["items"])
    assert "Marked milk as done" in rig.say("the second one")


def test_unknown_list_offers_to_make_it_and_waits_for_a_yes(rig):
    r = rig.say("add candles to my party list")
    assert "Want me to make one" in r and rig.store.lists() == []
    r = rig.say("yes")
    assert "ready" in r and rig.store.find_list("party")[0]["total"] == 1


def test_remind_me_is_not_pretended_to_work_yet(rig):
    r = rig.say("remind me to buy batteries tomorrow")
    assert "can't set reminders yet" in r and rig.store.notes() == []


def test_ordinary_speech_is_left_alone(rig):
    for t in ("how was your day", "I need to go to the shop", "tell me a joke", "mark the calendar", "what's on tv tonight",
              "I added a note to my diary", "my list of favourite songs is long"):
        assert rig.say(t) is None, t


def test_split_items():
    assert split_items("We need eggs, rice, milk, and fruit.") == ["eggs", "rice", "milk", "fruit"]
    assert split_items("some bananas and a few apples") == ["bananas", "apples"]
    assert split_items(": tape; glue & paper") == ["tape", "glue", "paper"]


def test_dictation_that_goes_quiet_is_dropped_without_claiming_anything(rig):
    rig.say("save a voice note", "half a thought")
    rig.clock.t += 16 * 60
    assert rig.talk.handle("done") is None and rig.store.notes() == []


def test_a_safety_takeover_cancels_the_draft(rig):
    rig.say("save a voice note", "half a thought")
    rig.talk.cancel()
    assert not rig.talk.dictating and rig.store.notes() == []
