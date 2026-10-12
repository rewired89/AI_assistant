from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from companion.conversation.safety import violations
from companion.productivity import ProductivityStore, StoreError
from companion.productivity.reminders import LINES, ReminderStore, category, compose

NY = ZoneInfo("America/New_York")


def at(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=NY).timestamp()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    clock = Clock(at(2026, 10, 12, 9))
    store = ProductivityStore(tmp_path / "p.db", clock)
    rs = ReminderStore(store, clock, "America/New_York")
    yield rs, clock
    store.close()


def lunch(rs, **kw):
    return rs.create(title="Eat something", kind="daily", time="15:00", **kw)


def test_a_daily_3pm_reminder_fires_once_a_day_with_a_stable_occurrence_id(env):
    rs, c = env
    r = lunch(rs)
    assert r["status"] == "scheduled" and r["schedule_text"] == "every day at 3:00 PM" and r["next_at"] == at(2026, 10, 12, 15)
    c.t = at(2026, 10, 12, 14, 59)
    assert rs.tick() == []
    c.t = at(2026, 10, 12, 15, 0)
    made = rs.tick()
    assert len(made) == 1 and made[0]["id"] == f"{r['id']}:{int(at(2026, 10, 12, 15))}" and made[0]["robot"] == "pending" and made[0]["phone"] == "pending"
    assert rs.tick() == [] and rs.tick(at(2026, 10, 12, 15, 5)) == []                         # repeated ticks never duplicate
    c.t = at(2026, 10, 13, 15, 1)
    assert len(rs.tick()) == 1 and len(rs.occurrences()) == 2


def test_a_restart_cannot_duplicate_an_occurrence(env, tmp_path):
    rs, c = env
    r = lunch(rs)
    c.t = at(2026, 10, 12, 15, 1)
    rs.tick()
    rs2 = ReminderStore(rs.s, c, "America/New_York")                                          # a second instance over the same data (a restart)
    assert rs2.tick() == [] and len(rs2.occurrences()) == 1
    rs.s.db.execute("UPDATE reminders SET cursor_at=? WHERE id=?", (at(2026, 10, 12, 9), r["id"]))      # even a lost cursor cannot double it
    rs.s.db.commit()
    assert rs2.tick() == [] and len(rs2.occurrences()) == 1


def test_editing_changes_the_same_reminder_and_never_creates_a_second(env):
    rs, c = env
    r = lunch(rs)
    c.t = at(2026, 10, 12, 15, 1)
    rs.tick()
    u = rs.update(r["id"], time="16:30", message="Hi {name}, snack?")
    assert u["id"] == r["id"] and len(rs.reminders()) == 1 and u["schedule_text"] == "every day at 4:30 PM"
    assert u["next_at"] == at(2026, 10, 12, 16, 30)                                            # a new time starts from now: nothing replayed
    c.t = at(2026, 10, 12, 16, 31)
    made = rs.tick()
    assert len(made) == 1 and made[0]["due_at"] == at(2026, 10, 12, 16, 30)
    assert len(rs.occurrences()) == 2
    rs.update(r["id"], title="Eat lunch")                                                      # a wording change keeps the schedule untouched
    assert rs.get(r["id"])["next_at"] == at(2026, 10, 13, 16, 30)


def test_pause_stops_deliveries_and_resume_does_not_replay_the_pause(env):
    rs, c = env
    r = lunch(rs)
    rs.set_enabled(r["id"], False)
    assert rs.get(r["id"])["status"] == "paused" and rs.get(r["id"])["next_at"] is None
    c.t = at(2026, 10, 14, 16)
    assert rs.tick() == []
    rs.set_enabled(r["id"], True)
    assert rs.tick() == [] and rs.get(r["id"])["next_at"] == at(2026, 10, 15, 15)


def test_one_time_reminder_fires_then_is_completed(env):
    rs, c = env
    r = rs.create(title="Buy batteries", kind="once", date="2026-10-13", time="10:30")
    assert r["status"] == "scheduled"
    c.t = at(2026, 10, 13, 10, 31)
    assert len(rs.tick()) == 1
    assert rs.get(r["id"])["status"] == "completed" and rs.tick(at(2026, 10, 20)) == []
    past = rs.create(title="Old", kind="once", date="2026-10-01", time="10:00")
    rs.tick()
    assert rs.get(past["id"])["status"] in ("completed", "ended") and rs.occurrences(10, past["id"]) == []


def test_overdue_moments_are_not_replayed_in_a_burst(env):
    rs, c = env
    r = lunch(rs)
    c.t = at(2026, 10, 17, 15, 10)                                                             # the host was off for five days
    made = rs.tick()
    assert len(made) == 6                                                                      # Oct 12 to Oct 17
    due = rs.due_for_robot()
    assert [o["due_at"] for o in due] == [at(2026, 10, 17, 15)]                                # only today's is still speakable
    old = [o for o in rs.occurrences() if o["due_at"] < at(2026, 10, 17)]
    assert len(old) == 5 and all(o["status"] == "missed" and o["robot"] == "unavailable" for o in old)


def test_grace_period_then_missed_and_the_phone_still_shows_it(env):
    rs, c = env
    r = lunch(rs, grace_minutes=30)
    c.t = at(2026, 10, 12, 15, 1)
    rs.tick()
    assert len(rs.due_for_robot()) == 1
    c.t = at(2026, 10, 12, 15, 31)
    rs.tick()
    assert rs.due_for_robot() == []
    o = rs.occurrences()[0]
    assert o["status"] == "missed" and o["robot"] == "unavailable" and o["phone"] == "pending"
    assert [p["id"] for p in rs.phone_pending()] == [o["id"]]
    rs.set_settings(fallback="skip")
    assert rs.phone_pending() == []                                                            # her fallback choice: skip, don't surface


def test_robot_delivery_is_a_recorded_state_never_assumed(env):
    rs, c = env
    r = lunch(rs)
    c.t = at(2026, 10, 12, 15, 1)
    o = rs.tick()[0]
    assert rs.get(r["id"])["last_delivery"].startswith("waiting for Nine Tails") or "waiting for Nine Tails" in rs.get(r["id"])["last_delivery"]
    rs.mark_robot(o["id"], "speaking")
    assert rs.get_occurrence(o["id"])["status"] == "pending"                                   # speaking is not delivered
    rs.mark_robot(o["id"], "spoken", "Hey! It's around 3 PM.")
    got = rs.get_occurrence(o["id"])
    assert got["status"] == "delivered" and got["robot"] == "spoken" and got["spoken_text"] == "Hey! It's around 3 PM."
    assert "spoken by Nine Tails" in rs.get(r["id"])["last_delivery"]
    assert rs.due_for_robot() == []


def test_a_robot_that_never_spoke_is_reported_as_unavailable_not_delivered(env):
    rs, c = env
    r = lunch(rs, channels={"robot": True, "phone": False})
    c.t = at(2026, 10, 12, 15, 1)
    rs.tick()
    c.t = at(2026, 10, 12, 16)                                                                 # asleep, offline or busy the whole time
    rs.tick()
    o = rs.occurrences()[0]
    assert o["status"] == "missed" and o["robot"] == "unavailable"
    assert "could not say it" in rs.get(r["id"])["last_delivery"] and "spoken" not in rs.get(r["id"])["last_delivery"]


def test_quiet_hours_mean_silence_but_the_phone_still_has_it(env):
    rs, c = env
    rs.set_settings(quiet_start="22:00", quiet_end="07:00")
    r = rs.create(title="Go to bed", kind="daily", time="23:00")
    c.t = at(2026, 10, 12, 23, 1)
    o = rs.tick()[0]
    assert o["robot"] == "quiet" and o["phone"] == "pending" and rs.due_for_robot() == []
    assert rs.in_quiet_hours(at(2026, 10, 12, 6, 59)) and not rs.in_quiet_hours(at(2026, 10, 12, 7, 0))


def test_channels_and_the_global_switches(env):
    rs, c = env
    rs.create(title="Phone only", kind="daily", time="10:00", channels={"robot": False, "phone": True})
    rs.create(title="Robot only", kind="daily", time="10:00", channels={"robot": True, "phone": False})
    rs.create(title="Quiet style", kind="daily", time="10:00", style="quiet")
    c.t = at(2026, 10, 12, 10, 1)
    occ = {rs.get(o["reminder_id"])["title"]: o for o in rs.tick()}
    assert occ["Phone only"]["robot"] == "n/a" and occ["Phone only"]["phone"] == "pending"
    assert occ["Robot only"]["robot"] == "pending" and occ["Robot only"]["phone"] == "n/a"
    assert occ["Quiet style"]["robot"] == "n/a"
    rs.set_settings(speak=False)
    rs.create(title="Later", kind="daily", time="11:00")
    c.t = at(2026, 10, 12, 11, 1)
    assert rs.tick()[0]["robot"] == "n/a"                                                      # spoken reminders switched off in settings


def test_snooze_makes_one_new_occurrence_and_a_snooze_of_a_snooze_does_not_stack(env):
    rs, c = env
    r = lunch(rs)
    c.t = at(2026, 10, 12, 15, 1)
    o = rs.tick()[0]
    s1 = rs.snooze(o["id"], 10)
    assert s1["due_at"] == c.t + 600 and rs.get_occurrence(o["id"])["status"] == "snoozed" and s1["snooze_of"] == o["id"]
    c.t += 601
    rs.tick()
    assert [x["id"] for x in rs.due_for_robot()] == [s1["id"]]
    s2 = rs.snooze(s1["id"], 5)
    assert s2["id"].endswith("~s2") and rs.get_occurrence(s1["id"])["status"] == "snoozed"
    assert [x["id"] for x in rs.occurrences() if x["status"] == "pending"] == [s2["id"]]
    with pytest.raises(StoreError):
        rs.snooze(s2["id"], 0)
    with pytest.raises(StoreError):
        rs.snooze(s2["id"], until=c.t - 5)


def test_dismiss_and_skip_next(env):
    rs, c = env
    r = lunch(rs)
    c.t = at(2026, 10, 12, 15, 1)
    o = rs.tick()[0]
    assert rs.acknowledge(o["id"]) and rs.get_occurrence(o["id"])["status"] == "acknowledged" and rs.due_for_robot() == []
    skipped = rs.skip_next(r["id"])
    assert skipped["next_at"] == at(2026, 10, 14, 15)                                          # tomorrow's is skipped
    c.t = at(2026, 10, 13, 15, 5)
    assert rs.tick() == [] and any(x["status"] == "skipped" for x in rs.occurrences())


def test_phone_sees_and_acknowledges(env):
    rs, c = env
    r = lunch(rs)
    c.t = at(2026, 10, 12, 15, 1)
    o = rs.tick()[0]
    p = rs.phone_pending()
    assert p[0]["title"] == "Eat something" and "3" in p[0]["text"]
    assert rs.mark_phone_seen(o["id"]) and not rs.mark_phone_seen(o["id"]) and rs.phone_pending() == []
    assert rs.get_occurrence(o["id"])["status"] == "delivered"


def test_priority_orders_what_the_robot_says_first(env):
    rs, c = env
    rs.create(title="Low one", kind="daily", time="10:00", priority="low")
    rs.create(title="High one", kind="daily", time="10:00", priority="high")
    c.t = at(2026, 10, 12, 10, 1)
    rs.tick()
    assert [rs.get(o["reminder_id"])["title"] for o in rs.due_for_robot()] == ["High one", "Low one"]


def test_dst_changes_are_followed_by_the_ticks_themselves(env):
    rs, c = env
    c.t = at(2026, 3, 7, 12)
    r = rs.create(title="Water", kind="daily", time="15:00")
    seen = []
    for day in (7, 8, 9):
        c.t = at(2026, 3, day, 15, 1)
        seen += [datetime.fromtimestamp(o["due_at"], NY).strftime("%m-%d %H:%M") for o in rs.tick()]
    assert seen == ["03-07 15:00", "03-08 15:00", "03-09 15:00"]
    c.t = at(2026, 11, 1, 12)                                                                  # fall back: the 1:30 AM that happens twice
    r2 = rs.create(title="Night", kind="daily", time="01:30")
    n = 0
    for h in range(0, 30):
        c.t = at(2026, 11, 1, 12) + h * 3600
        n += len([o for o in rs.tick() if o["reminder_id"] == r2["id"]])
    assert n == 1                                                                              # exactly one 1:30 across the 25-hour stretch


def test_delete_removes_its_history_and_duplicate_makes_an_independent_copy(env):
    rs, c = env
    r = lunch(rs)
    c.t = at(2026, 10, 12, 15, 1)
    rs.tick()
    d = rs.duplicate(r["id"])
    assert d["id"] != r["id"] and d["title"] == "Eat something (copy)" and len(rs.reminders()) == 2
    assert rs.delete(r["id"]) and rs.occurrences(10, r["id"]) == [] and len(rs.reminders()) == 1 and not rs.delete(r["id"])


def test_upcoming_week_view_is_sorted_and_respects_pauses(env):
    rs, c = env
    a = rs.create(title="A", kind="daily", time="10:00")
    rs.create(title="B", kind="weekly", time="09:00", weekdays=[0])
    paused = rs.create(title="C", kind="daily", time="08:00")
    rs.set_enabled(paused["id"], False)
    up = rs.upcoming(days=7)
    assert [u["title"] for u in up].count("A") == 7 and "C" not in [u["title"] for u in up] and up == sorted(up, key=lambda x: x["at"])


@pytest.mark.parametrize("bad", [
    {"title": ""}, {"title": "x" * 81}, {"style": "angry"}, {"priority": "urgent"}, {"snooze_minutes": 0}, {"grace_minutes": 1},
    {"message": "Hi {secret}"}, {"message": "x" * 201}, {"time": "99:00"}, {"channels": {"carrier_pigeon": True}}, {"kind": "weekly", "weekdays": []}])
def test_bad_reminders_are_refused(env, bad):
    rs, _ = env
    f = {"title": "Ok", "kind": "daily", "time": "15:00", **bad}
    with pytest.raises(StoreError):
        rs.create(**f)
    assert rs.reminders() == []


def test_titles_and_messages_are_encrypted_at_rest(tmp_path):
    clock = Clock(at(2026, 10, 12, 9))
    s = ProductivityStore(tmp_path / "e.db", clock, passphrase="correct horse battery")
    rs = ReminderStore(s, clock, "America/New_York")
    r = rs.create(title="Call the surprise party planner", kind="daily", time="10:00", message="Psst {name}, the secret call")
    s.close()
    raw = (tmp_path / "e.db").read_bytes()
    assert b"surprise party" not in raw and b"secret call" not in raw
    s2 = ProductivityStore(tmp_path / "e.db", clock, passphrase="correct horse battery")
    assert ReminderStore(s2, clock).get(r["id"])["title"] == "Call the surprise party planner"


# ---- spoken wording --------------------------------------------------------------------------------------
def test_wording_comes_from_her_reminder_and_style_and_never_changes_the_time(env):
    rs, _ = env
    r = lunch(rs)
    t = at(2026, 10, 12, 15)
    gentle = compose({**r, "style": "gentle"}, t, "Sam")
    playful = compose({**r, "style": "playful"}, t, "Sam")
    assert "3 PM" in gentle and "eat" in gentle.lower() and gentle != playful
    assert compose({**r, "style": "plain"}, t, None) == "Reminder: Eat something."
    assert compose({**r, "style": "direct"}, t, None) == "It's around 3 PM. Eat something."
    assert compose({**r, "message": "Hi {name}! {title} at {time}."}, t, "Sam") == "Hi Sam! Eat something at around 3 PM."
    assert compose({**r, "style": "gentle"}, t, "Sam") == gentle                                 # deterministic for one moment
    days = {compose({**r, "style": "gentle"}, t + 86400 * i, "Sam") for i in range(6)}
    assert len(days) >= 2                                                                      # not identical every day


def test_categories_and_the_spec_examples():
    assert category("Eat something") == "food" and category("Study Russian") == "study" and category("Exercise") == "exercise"
    assert category("Work on my project") == "project" and category("Go to bed") == "bed" and category("Buy batteries") == "generic"
    t = at(2026, 10, 12, 21)
    assert "winding down" in compose({"id": "0000aaaa", "title": "Go to bed", "message": "", "style": "gentle", "tz": "America/New_York"}, t, None, variant=0)


def test_no_scripted_line_uses_guilt_pressure_or_claims_a_body():
    import re
    bad_body = re.compile(r"\b(i'?m|i am) (hungry|tired|thirsty|sleepy|starving|hurt|sad|lonely)\b|\bi (miss|need) you\b|\byou (forgot|didn'?t|never|should have)\b|\bagain\b", re.I)
    for style, cats in LINES.items():
        for cat, lines in cats.items():
            assert len(lines) >= 3
            for line in lines:
                text = line.format(t="around 3 PM", title="the thing")
                assert not violations(text) and not bad_body.search(text), (style, cat, text)
