# Phone app: notes, lists and her private journal

A web app she opens on her phone (and can add to the home screen). It reads and writes the same notes and lists the robot's voice commands use,
and shows the journal entries she chose to keep. Soft teal and pink frosted glass, rounded panels, a floating round-icon dock on phones and a left rail on wide screens, built for a small screen.

## See it now (sample data, no login)
```powershell
git pull origin main
./.venv/bin/Activate.ps1
python -m tools.app demo
```
Open `http://localhost:8790`. On a desktop browser, make the window narrow (or press F12 and choose the phone view) to see the phone layout.
Everything in demo mode is SAMPLE content, listens only on this computer, and is gone when you stop it.

## Run it for real
1. In the companion config add (this computer only, for testing on the laptop):
```yaml
webapp:
  enabled: true
  host: 127.0.0.1
  port: 8790
```
   With the desk robot: `python -m tools.desk_robot init --dir dev ... --phone-app`.
2. Start the companion. Print a one-time pairing code: `python -m tools.app pair --config dev/companion.yaml`.
3. Open `http://localhost:8790`, enter the code, and CHOOSE the passphrase (8+ characters). The owner never sees it.
4. On the Minisforum for her phone: set `host: 0.0.0.0`, `hosts: ["192.168.1.42"]` (the address her phone types). The app then serves over HTTPS using the
   companion's certificate, so her phone must trust the private CA (`tls/ca.pem`): Android: Settings > Security > Install a certificate (CA); iPhone: install the
   profile, then Settings > General > About > Certificate Trust Settings. Without that her phone shows a warning. The app refuses to listen beyond this computer without TLS.
5. On the phone open `https://192.168.1.42:8790`, log in, then Share > Add to Home Screen.

## Voice commands (all scripted; no language model sees them)
- Notes: "write this down: ...", "remember this idea. ...", "save a voice note" (then "done", "pause", "continue", "cancel"), "call it Robot enclosure",
  "read my last note", "find the note about the robot", "what did I write down yesterday", "delete that note" (asks first).
- Dictation: word for word by default; "clean up my dictation" tidies spacing, capitals and the full stop only; "keep my dictation word for word" turns it off.
- Lists: "make me a grocery list. We need eggs, rice, milk and fruit", "add bananas to my grocery list", "what's on my grocery list", "mark eggs as complete",
  "uncheck eggs". If a command could touch the wrong list or item, it asks which. Talking about a task never completes it.
- "Saved" is only said after the store reports success. If saving fails the draft stays in memory and she can say "done" again.

## What the app shows
Home (quick note, recent notes, lists, journal count), Notes (search, filters, edit, append, archive, export, delete), Lists (check off, restore, add, rename, reorder, filter, delete),
Journal (her kept entries, read and delete), Settings (colour mode, dictation tidy, export everything, delete everything, change passphrase, log out, honest privacy note).

## Security (tested)
Pairing code then her own passphrase (scrypt); rate-limited login; HttpOnly SameSite=Strict session cookie (Secure over TLS) that expires when idle; every write needs a custom header and
same-origin; the Host header must be ours; size-limited JSON; her text is shown with `textContent` only; strict Content-Security-Policy.

## Honest limits
- Not end-to-end encrypted. Whoever administers the host can read its files. `memory.encrypt: true` encrypts notes and lists at rest with the memory passphrase, which protects a stolen disk,
  not the administrator. A design where only her passphrase can open the data is possible but would stop the robot reading notes aloud while she is logged out; not built.
- No audio is recorded or kept yet, so a "voice note" is text from the speech recognizer. Keeping the recording, playing it back, and "transcription pending" need the pipeline to hand audio to the store.
- Summaries (separate from the note) need the language model: not built. Reading a note aloud from the phone, and sharing, are not built.
- Reminders are built (see `docs/reminders.md`). Phone alerts work only while the app is open: a web app cannot schedule notifications while closed. Web push or a Capacitor wrapper would fix that; neither is built or claimed.
- Tested with Python HTTP tests and Chromium screenshots on a phone-sized window; not yet tried on a real phone.
