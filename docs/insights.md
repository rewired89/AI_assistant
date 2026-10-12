# Daily Psychology Discoveries (spec 45 to 54)

A short, true, interesting piece of psychology research a day. Education, not coaching: it never tells her what to change.

## How it works
- Library: one YAML file per entry in `content/discoveries/`, fields as in spec 53 plus a `verification` record. `python -m tools.insights check|show|verify|unverify`.
- Only entries with `verification.status: verified` can ever be spoken. A verified entry must name the researcher and year, give a source title and an https link, state its limitations, and pass the content rules (no guilt, diagnosis, pressure, "you always", partner framing; a reflection question that is a question; 30 to 90 seconds when spoken). Failing files are dropped, never delivered.
- The scientific wording lives in the entry. No language model writes or alters a claim; follow-ups (source, tiny exercise, what the research cannot tell us) are read from the same entry.
- Off until she turns it on ("turn on daily insights"); that reply says what it is. One a day inside a 4 hour window after her chosen time, only when the robot is idle and awake; a missed day is skipped. No reminders, no streaks, no scores. State is `data/insights.json` (settings, what was shown/saved/skipped), never her words.
- Her voice controls (scripted): turn on/off, pause/resume, "set the insights time to 6 pm", "send insights as text|voice|both", "only habits insights", "any topics", "include relationship topics", "tell me a psychology discovery", "another one", then within 15 minutes: "where does that come from", "tiny exercise", "tell me more", "save that", "skip"; later: "what insights have I saved", "revisit <title>".
- Personalization (spec 50) is limited to topics she names herself. Nothing is chosen from what she told the robot privately, and relationship/conflict entries are served only if she asks for them.
- Independent of firmware (criterion 9): pure host logic. Text delivery goes to an inbox in the state file; showing it on the robot's screen needs the board (UNVERIFIED).

## Verification status today (honest)
Six entries are marked verified by Claude on 2026-10-12 at ABSTRACT level: the bibliographic details were matched against independent pages (PubMed, journal records, university repositories) and each summary was kept inside what the abstract says. I did not read the full papers. Entries say `owner_confirmed: false`: skim the sources and run `python -m tools.insights verify ID --by "Your Name" --basis "..."` to confirm. Three drafts from the spec (break-and-return, identity and change, adapting old strategies) name no researcher, so they are unverified and cannot be delivered until someone finds and checks a real source.
Notes: the 66-day habit number is deliberately not stated as a rule; Gollwitzer effect sizes are not quoted; the Neff link is an aggregator record (publisher DOI not opened).

## Decisions
- Scripted from verified content, not generated: a model could "improve" a finding into a claim nobody checked (spec 47). Cost: less variety; the library has to grow by hand.
- Balanced rotation by topic (longest unused topic first) so it is not only about weaknesses or relationships (criterion 8). With six entries over five topics the rotation is thin; it needs more verified entries.
- Not built: contextual personalization beyond named topics, the text display on the device, entries for all eight categories (relationships/conflict and identity have drafts only).
