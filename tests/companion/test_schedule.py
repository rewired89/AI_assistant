from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from companion.productivity import schedule as S

NY = ZoneInfo("America/New_York")


def ts(y, mo, d, h=0, mi=0, tz=NY):
    return datetime(y, mo, d, h, mi, tzinfo=tz).timestamp()


def local(t, tz=NY):
    return datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d %H:%M")


def rem(**kw):
    base = {"kind": "daily", "time": "15:00", "tz": "America/New_York", "weekdays": []}
    base.update(kw)
    return S.validate(base)


def test_daily_3pm_next_occurrences():
    r = rem()
    assert local(S.next_due(r, ts(2026, 10, 12, 14))) == "2026-10-12 15:00"
    assert local(S.next_due(r, ts(2026, 10, 12, 15, 0))) == "2026-10-13 15:00"           # strictly after
    assert [local(t) for t in S.upcoming(r, ts(2026, 10, 12, 16), 3)] == ["2026-10-13 15:00", "2026-10-14 15:00", "2026-10-15 15:00"]


def test_weekly_monday_wednesday_friday_6pm():
    r = rem(kind="weekly", time="18:00", weekdays=[0, 2, 4])
    assert [local(t) for t in S.upcoming(r, ts(2026, 10, 12, 19), 4)] == ["2026-10-14 18:00", "2026-10-16 18:00", "2026-10-19 18:00", "2026-10-21 18:00"]


def test_one_time_runs_once_and_never_again():
    r = rem(kind="once", date="2026-10-13", time="10:30")
    first = S.next_due(r, ts(2026, 10, 12, 9))
    assert local(first) == "2026-10-13 10:30" and S.next_due(r, first) is None
    assert S.next_due(r, ts(2026, 10, 14)) is None


def test_start_and_end_dates_bound_the_schedule():
    r = rem(start_date="2026-10-20", end_date="2026-10-22")
    assert local(S.next_due(r, ts(2026, 10, 12))) == "2026-10-20 15:00"
    assert [local(t) for t in S.upcoming(r, ts(2026, 10, 12), 10)] == ["2026-10-20 15:00", "2026-10-21 15:00", "2026-10-22 15:00"]


def test_every_two_hours_between_9_and_5():
    r = rem(kind="interval", interval_minutes=120, window_start="09:00", window_end="17:00")
    assert [local(t)[-5:] for t in S.upcoming(r, ts(2026, 10, 12, 8), 6)] == ["09:00", "11:00", "13:00", "15:00", "17:00", "09:00"]
    r2 = rem(kind="interval", interval_minutes=120, window_start="09:00", window_end="17:00", weekdays=[0, 1, 2, 3, 4])
    assert local(S.next_due(r2, ts(2026, 10, 9, 18))) == "2026-10-12 09:00"                # Friday evening -> Monday morning


def test_three_pm_stays_three_pm_across_the_spring_forward_change():
    r = rem()                                                                              # US clocks go forward on 2026-03-08
    a, b = S.next_due(r, ts(2026, 3, 7, 16)), S.next_due(r, ts(2026, 3, 8, 16))
    assert local(a) == "2026-03-08 15:00" and local(b) == "2026-03-09 15:00"
    assert datetime.fromtimestamp(a, timezone.utc).hour == 19                           # 3 PM daylight time = 19:00 UTC
    assert datetime.fromtimestamp(S.next_due(r, ts(2026, 3, 6, 16)), timezone.utc).hour == 20   # 3 PM standard time = 20:00 UTC
    assert b - a == 24 * 3600


def test_three_pm_stays_three_pm_across_the_fall_back_change():
    r = rem()                                                                              # 2026-11-01
    a = S.next_due(r, ts(2026, 10, 31, 16))
    b = S.next_due(r, a)
    assert local(a) == "2026-11-01 15:00" and local(b) == "2026-11-02 15:00"
    assert a - ts(2026, 10, 31, 15) == 25 * 3600                                           # the day is an hour longer; the wall time is the same


def test_a_time_that_does_not_exist_runs_once_at_the_shifted_moment():
    r = rem(time="02:30")                                                                  # 2026-03-08 02:30 does not exist in New York
    due = S.upcoming(r, ts(2026, 3, 7, 12), 3)
    assert [local(t) for t in due] == ["2026-03-08 03:30", "2026-03-09 02:30", "2026-03-10 02:30"]
    assert len({round(t) for t in due}) == 3


def test_a_time_that_happens_twice_runs_once():
    r = rem(time="01:30")                                                                  # 2026-11-01 01:30 happens twice
    due = S.upcoming(r, ts(2026, 10, 31, 12), 3)
    first = datetime(2026, 11, 1, 1, 30, tzinfo=NY, fold=0).timestamp()
    assert [local(t) for t in due] == ["2026-11-01 01:30", "2026-11-02 01:30", "2026-11-03 01:30"] and due[0] == first
    assert S.next_due(r, first) > ts(2026, 11, 2, 0)                                       # the second 1:30 an hour later is skipped


def test_each_reminder_keeps_its_own_time_zone():
    tokyo = rem(tz="Asia/Tokyo")
    ny = rem()
    t_ny, t_tk = S.next_due(ny, ts(2026, 10, 12, 0)), S.next_due(tokyo, ts(2026, 10, 12, 0))
    assert t_ny != t_tk and local(t_tk, ZoneInfo("Asia/Tokyo")).endswith("15:00")
    southern = rem(tz="Australia/Sydney", time="09:00")                                    # DST goes the other way in October
    a = S.next_due(southern, datetime(2026, 10, 3, 12, tzinfo=timezone.utc).timestamp())
    assert local(a, ZoneInfo("Australia/Sydney")).endswith("09:00")


@pytest.mark.parametrize("bad", [
    {"kind": "sometimes"}, {"tz": "Mars/Base"}, {"time": "25:00"}, {"time": "3pm"}, {"weekdays": [7]}, {"weekdays": ["Mon"]},
    {"kind": "weekly", "weekdays": []}, {"kind": "once", "date": None}, {"kind": "once", "date": "2026-02-30"},
    {"start_date": "2026-10-10", "end_date": "2026-10-01"}, {"kind": "interval", "interval_minutes": 1, "window_start": "09:00", "window_end": "17:00"},
    {"kind": "interval", "interval_minutes": 60, "window_start": "17:00", "window_end": "09:00"}])
def test_impossible_schedules_are_refused(bad):
    base = {"kind": "daily", "time": "15:00", "tz": "America/New_York", "weekdays": [], "date": "2026-10-13", "interval_minutes": 60,
            "window_start": "09:00", "window_end": "17:00"}
    base.update(bad)
    with pytest.raises(S.ScheduleError):
        S.validate(base)


def test_plain_words_for_the_schedule():
    assert S.describe(rem()) == "every day at 3:00 PM"
    assert S.describe(rem(kind="weekly", time="18:00", weekdays=[0, 2, 4])) == "every Monday, Wednesday and Friday at 6:00 PM"
    assert S.describe(rem(kind="weekly", time="08:00", weekdays=[0, 1, 2, 3, 4])) == "every weekday at 8:00 AM"
    assert S.describe(rem(kind="weekly", time="20:00", weekdays=[6])) == "every Sunday at 8:00 PM"
    assert S.describe(rem(kind="once", date="2026-10-13", time="10:30")) == "Tuesday, October 13 at 10:30 AM"
    assert S.describe(rem(kind="interval", interval_minutes=120, window_start="09:00", window_end="17:00")) == "every 2 hours from 9:00 AM to 5:00 PM"
