# Reminders

Nine Tails says them out loud, in her own words. The phone app shows them too while it is open.

## Say it
- "Remind me to take my vitamins at 9" (she asks am or pm if unclear), "every day at 8", "weekdays at 7:30", "every 2 hours", "in 20 minutes", "tomorrow at 3".
- "What are my reminders", "pause the vitamins one", "resume ...", "delete ... " (asks first).
- Right after she speaks one: "snooze", "okay" (done), "skip it". These only count within 10 minutes of her speaking.
- She says "set" only after it is saved. Words about "I can't set reminders" are gone.

## Phone app (Reminders tab)
Coming up / All / History, an editor (repeat once, every day, certain days, hours; time; date; window; start/end date; time zone; who delivers; style; your own wording with
{name} {title} {time}; "Hear a few examples" shows sample lines; importance, snooze, missed-after minutes), pause, resume, duplicate, skip next, delete.
Settings: spoken on/off, phone on/off, if she could not say one (show on phone or let it go), default style, quiet hours, time zone, alert permission.

## How delivery works
- One row per reminder; each moment is an occurrence with the id `reminderid:time`, created once (no duplicates after restarts or edits).
- Statuses: pending, delivered, acknowledged, snoozed, skipped, missed. Robot: pending, speaking, spoken, unavailable, quiet, failed. Phone: pending, shown.
- The robot speaks only when awake, idle, connected, battery ok and no safety hold. "Spoken" is recorded only after playback finished.
- Past the grace period (default 30 min) it becomes missed; only the newest overdue moment can still be spoken.
- Quiet hours silence speech; the phone still shows it. Snooze makes one new moment (no stacking).
- Wording is scripted (no language model), never guilt or claims about her body. Styles: auto, plain, direct, gentle, playful, quiet.

## Time zones and DST
Each reminder has its own time zone and keeps its wall-clock time across DST. A time that does not exist (spring forward) runs once at the shifted moment;
a repeated hour (fall back) runs once, at the first pass.

## Honest limits
- A web app cannot wake a phone while closed. Alerts show in the app, and as a system alert only if allowed and the app is open. Reliable phone alerts need web push (internet plus a push service) or a Capacitor wrapper. Not built, not claimed.
- Tested with fake clocks, the simulated robot, HTTP tests and headless Chromium. Not on a real robot or phone.
- Not built: audio retention, summaries, read-aloud from the phone.
