"""Nine Tails phone app (notes, lists, private journal).

    python -m tools.app demo            # try it now with SAMPLE data, no login: open http://localhost:8790
    python -m tools.app pair --config dev/companion.yaml  # print a one-time pairing code (she uses it to choose her own passphrase)

Demo mode only ever listens on this computer and keeps everything in memory; nothing is saved."""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

from datetime import datetime, timedelta, timezone

from companion.productivity import ProductivityStore
from companion.productivity.reminders import ReminderStore
from companion.webapp import AuthStore, Journal, WebApp


class SampleJournal(Journal):
    def __init__(self, now: float):
        d = 86400
        self.rows = [{"id": "s1", "created_at": now - 1 * d, "text": "Today: I finished the painting I started last week and then went for a long walk.", "label": "checkin 2026-10-11"},
                     {"id": "s2", "created_at": now - 4 * d, "text": "Today: Work was hectic, but lunch with Mia made it better.", "label": "checkin 2026-10-08"}]

    def entries(self):
        return sorted(self.rows, key=lambda r: -r["created_at"])

    def delete(self, entry_id):
        n = len(self.rows)
        self.rows = [r for r in self.rows if r["id"] != entry_id]
        return len(self.rows) < n

    def clear(self):
        n, self.rows = len(self.rows), []
        return n


def seed(store: ProductivityStore) -> None:
    """Clearly fake content so the screens have something to show."""
    c = store.clock
    base = c()
    store.clock = lambda: base - 3 * 3600
    store.add_note("I want to try making a little robot enclosure with removable magnetic panels. Maybe two colours, teal and pink.", kind="voice", source="voice",
                   title="Robot enclosure idea", tags=["idea", "robot"], transcript_state="done")
    store.clock = lambda: base - 26 * 3600
    store.add_note("I need to research how to make the sprite animations use less memory.", kind="dictated", source="voice", tags=["research"])
    store.clock = lambda: base - 3 * 86400
    store.add_note("Things I liked this week:\n- the walk by the river\n- finishing the painting\n- Mia's soup", kind="written", title="Good things", tags=["gratitude"])
    store.clock = c
    g = store.create_list("Groceries", ["eggs", "rice", "milk", "fruit", "bananas"])
    store.update_item(g["items"][0]["id"], done=True)
    store.update_item(g["items"][1]["id"], done=True)
    store.create_list("Robot project", ["order batteries", "print the enclosure", "test the speaker"])
    store.create_list("Daily tasks", ["exercise", "study Russian", "drink water"])


def seed_reminders(rs: ReminderStore) -> None:
    """Sample reminders (times in UTC so the demo works anywhere). One is due right now so the banner shows."""
    now = datetime.now(timezone.utc)
    at = lambda m: (now + timedelta(minutes=m)).strftime("%H:%M")
    real = rs.clock
    rs.clock = lambda: real() - 300                       # created five minutes ago, so this one has just gone off
    rs.create(title="Call Mia back", kind="once", date=(now - timedelta(minutes=2)).strftime("%Y-%m-%d"), time=(now - timedelta(minutes=2)).strftime("%H:%M"), tz="UTC")
    rs.clock = real
    rs.create(title="Take vitamins", kind="daily", time=at(95), tz="UTC", style="gentle")
    rs.create(title="Study Russian", kind="weekly", weekdays=[0, 2, 4], time=at(240), tz="UTC", style="playful")
    rs.create(title="Drink water", kind="interval", interval_minutes=120, window_start="09:00", window_end="21:00", tz="UTC")
    rs.create(title="Water the plants", kind="weekly", weekdays=[5], time="10:00", tz="UTC", priority="low")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo")
    d.add_argument("--port", type=int, default=8790)
    d.add_argument("--no-browser", action="store_true")
    p = sub.add_parser("pair")
    p.add_argument("--config", default="dev/companion.yaml", help="the companion's config file (its data folder holds the login)")
    a = ap.parse_args(argv)
    if a.cmd == "pair":
        from companion.app import AppConfig
        try:
            data = AppConfig.load(a.config).data_dir
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        code = AuthStore(data / "webapp_auth.json").create_pairing_code()
        print(f"Pairing code: {code}\nOpen the app, enter this code, and choose your own passphrase. The code works once and expires in one hour.")
        return 0
    tmp = tempfile.TemporaryDirectory()
    store = ProductivityStore(Path(tmp.name) / "demo.db", time.time)
    seed(store)
    rem = ReminderStore(store, time.time, "UTC")
    seed_reminders(rem)
    app = WebApp(store, SampleJournal(time.time()), None, time.time, port=a.port, demo=True, name=lambda: "Sam", reminders=rem)
    url = app.start()
    print(f"Nine Tails app (SAMPLE data, nothing is saved): {url}\nOpen that address in your browser. On a phone-sized window it looks like the real app. Ctrl+C stops it.")
    if not a.no_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
