from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from companion.productivity import ProductivityStore
from companion.productivity.remind_talk import RemindTalk, clean_title, parse_when
from companion.productivity.reminders import ReminderStore

NY = ZoneInfo("America/New_York")


def at(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=NY).timestamp()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def rig(tmp_path):
    c = Clock(at(2026, 10, 12, 16, 0))                       # Monday 4 PM
    st = ProductivityStore(tmp_path / "p.db", c)
    rs = ReminderStore(st, c, "America/New_York")
    t = RemindTalk(rs, c, "America/New_York", name=lambda: "Sam", phone_hint=True)

    class R:
        store, rems, talk, clock = st, rs, t, c

        def say(self, *us):
            out = []
            for u in us:
                self.clock.t += 3
                r = t.handle(u)
                out.append(r.text if r else None)
            return out if len(out) > 1 else out[0]
    yield R()
    st.close()


def now():
    return datetime.fromtimestamp(at(2026, 10, 12, 16), NY)


def test_parsing_the_spec_phrases():
    w, rest = parse_when("remind me to drink water every day at 3 pm", now())
    assert w.kind == "daily" and w.hm == (15, 0) and clean_title(rest) == "Drink water"
    w, rest = parse_when("remind me to exercise every Monday, Wednesday and Friday at 6:00 PM", now())
    assert w.kind == "weekly" and w.weekdays == [0, 2, 4] and w.hm == (18, 0) and clean_title(rest) == "Exercise"
    w, rest = parse_when("remind me to stretch every two hours between 9 AM and 5 PM", now())
    assert w.kind == "interval" and w.interval == 120 and w.window == (540, 1020) and clean_title(rest) == "Stretch"
    w, rest = parse_when("remind me to buy batteries tomorrow at 10:30 AM", now())
    assert w.kind == "once" and str(w.date) == "2026-10-13" and w.hm == (10, 30) and clean_title(rest) == "Buy batteries"
    w, rest = parse_when("remind me to call mom on Sunday at 8 pm", now())
    assert w.kind == "once" and str(w.date) == "2026-10-18" and w.hm == (20, 0)
    w, rest = parse_when("every weekday at 8 am remind me to prepare for work", now())
    assert w.weekdays == [0, 1, 2, 3, 4] and w.hm == (8, 0) and clean_title(rest) == "Prepare for work"
    w, rest = parse_when("remind me to study Russian every evening at 7", now())
    assert w.kind == "daily" and w.hm == (19, 0)                                      # "evening" settles the 7
    w, _ = parse_when("remind me at 15:30 to leave", now())
    assert w.hm == (15, 30)
    w, _ = parse_when("remind me at noon to eat", now())
    assert w.hm == (12, 0)


def test_numbers_in_the_title_are_not_times():
    w, rest = parse_when("remind me to buy 3 batteries tomorrow at 9 am", now())
    assert w.hm == (9, 0) and clean_title(rest) == "Buy 3 batteries"
    w, rest = parse_when("remind me to read chapter 5 every day at 7 pm", now())
    assert w.hm == (19, 0) and clean_title(rest) == "Read chapter 5"


def test_one_time_reminder_by_voice_is_saved_and_confirmed_in_plain_words(rig):
    r = rig.say("Remind me to buy batteries tomorrow at 10:30 AM")
    assert r == "Okay! I'll remind you to buy batteries tomorrow at 10:30 AM. You can see it on your phone too."
    rem = rig.rems.reminders()[0]
    assert rem["title"] == "Buy batteries" and rem["kind"] == "once" and rem["date"] == "2026-10-13" and rem["time"] == "10:30" and rem["tz"] == "America/New_York"


def test_recurring_by_voice(rig):
    assert "every day at 3 PM" in rig.say("remind me to eat something every day at 3 pm")
    assert "every Monday, Wednesday and Friday at 6 PM" in rig.say("remind me to exercise every Monday Wednesday and Friday at 6 pm")
    assert "every 2 hours from 9 AM to 5 PM" in rig.say("remind me to drink water every 2 hours between 9 and 5")
    assert len(rig.rems.reminders()) == 3


def test_a_time_today_that_has_passed_becomes_tomorrow_and_is_said_out_loud(rig):
    r = rig.say("remind me to water the plants at 3 pm")
    assert "tomorrow at 3 PM" in r and rig.rems.reminders()[0]["date"] == "2026-10-13"
    r = rig.say("remind me to call back at 6 pm")
    assert "today at 6 PM" in r and rig.rems.reminders()[1]["date"] == "2026-10-12"


def test_it_never_guesses_am_or_pm(rig):
    q = rig.say("remind me to take my medicine at 7")
    assert "morning or in the evening" in q and rig.rems.reminders() == []
    assert "7 PM" in rig.say("the evening")
    assert rig.rems.reminders()[0]["time"] == "19:00"


def test_it_asks_for_what_is_missing_instead_of_guessing(rig):
    assert rig.say("remind me to buy flowers") == "When should I remind you?"
    assert "tomorrow" in rig.say("tomorrow at 11 am") and rig.rems.reminders()[0]["time"] == "11:00"
    assert rig.say("remind me tomorrow") == "What should I remind you about?"
    assert "What time tomorrow" in rig.say("renew my passport") and "9:30 AM" in rig.say("9:30 am")
    assert [r["title"] for r in rig.rems.reminders()] == ["Buy flowers", "Renew my passport"]


def test_in_twenty_minutes(rig):
    r = rig.say("remind me in 20 minutes to check the oven")
    rem = rig.rems.reminders()[0]
    assert "Check the oven" == rem["title"] and rem["kind"] == "once"
    due = rig.rems.next_at(rem, rig.clock.t)
    assert 19 * 60 <= due - rig.clock.t <= 21 * 60 + 60


def test_nothing_is_claimed_when_the_store_refuses(rig):
    rig.rems.s.db.execute("DROP TABLE reminders")
    r = rig.say("remind me to eat every day at 3 pm")
    assert "couldn't set that reminder" in r and "Okay!" not in r


def test_list_pause_resume_and_delete_with_confirmation(rig):
    rig.say("remind me to drink water every day at 6 pm", "remind me to exercise every day at 7 pm")
    assert "Drink water, today at 6 PM" in rig.say("what are my reminders")
    assert "paused" in rig.say("pause my water reminder")
    assert rig.rems.find("water")[0]["status"] == "paused"
    assert "back on" in rig.say("resume my water reminder")
    assert "Do you want me to delete" in rig.say("delete the exercise reminder") and len(rig.rems.reminders()) == 2
    assert "kept it" in rig.say("no") and len(rig.rems.reminders()) == 2
    rig.say("delete the exercise reminder")
    assert "deleted" in rig.say("yes").lower() and [r["title"] for r in rig.rems.reminders()] == ["Drink water"]
    assert "couldn't find" in rig.say("delete the quantum reminder")


def test_ambiguous_names_ask_which(rig):
    rig.say("remind me to drink water every day at 6 pm", "remind me to drink tea every day at 7 pm")
    r = rig.say("pause my drink reminder")
    assert "Which one" in r and all(x["enabled"] for x in rig.rems.reminders())
    assert "tea" in rig.say("the second one").lower()
    assert [x["enabled"] for x in rig.rems.reminders()] == [True, False]


def test_snooze_dismiss_and_skip_only_make_sense_right_after_a_reminder_was_spoken(rig):
    rig.say("remind me to stretch every day at 5 pm")
    for u in ("snooze", "okay", "thanks", "skip it"):
        assert rig.say(u) is None                                                          # no reminder just spoken: ordinary speech
    rig.clock.t = at(2026, 10, 12, 17, 1)
    o = rig.rems.tick()[0]
    rig.talk.note_spoken(o["id"], rig.clock.t)
    r = rig.say("snooze for 15 minutes")
    assert "15 minutes" in r and rig.rems.get_occurrence(o["id"])["status"] == "snoozed"
    snoozed = [x for x in rig.rems.occurrences() if x["status"] == "pending"][0]
    assert snoozed["due_at"] == pytest.approx(rig.clock.t + 15 * 60, abs=5)
    assert rig.say("okay") == "Okay!" and rig.rems.get_occurrence(snoozed["id"])["status"] == "acknowledged"
    rig.clock.t += 11 * 60
    assert rig.say("okay") is None                                                         # the context expired


def test_snooze_without_a_length_uses_her_default_and_remind_me_again_at_a_time(rig):
    rig.say("remind me to stretch every day at 5 pm")
    rig.clock.t = at(2026, 10, 12, 17, 1)
    o = rig.rems.tick()[0]
    rig.talk.note_spoken(o["id"], rig.clock.t)
    assert "10 minutes" in rig.say("snooze")
    rig.talk.note_spoken(o["id"], rig.clock.t)
    assert "again at 6 PM" in rig.say("remind me again at 6 pm")


def test_ordinary_speech_is_left_alone(rig):
    for t in ("how was your day", "don't forget to call mom", "I remember when we met", "what time is it", "tell me a joke",
              "stop the music", "pause", "delete that note"):
        assert rig.say(t) is None, t
    assert rig.rems.reminders() == []


def test_the_reminder_is_created_in_her_time_zone(tmp_path):
    c = Clock(at(2026, 10, 12, 16))
    st = ProductivityStore(tmp_path / "p.db", c)
    rs = ReminderStore(st, c, "Asia/Tokyo")
    t = RemindTalk(rs, c, "Asia/Tokyo")
    t.handle("remind me to eat every day at 3 pm")
    assert rs.reminders()[0]["tz"] == "Asia/Tokyo"
