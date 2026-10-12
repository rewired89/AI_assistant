# Daily check-in and the private journal

What it is: once a day, at a time she chooses, the robot asks one question ("How was your day?") and listens. It is a companion
asking, not a therapist, and it never asks "what's wrong". Opt in by voice; she can stop it any time.

## Her voice controls (all scripted, no language model sees her answer)
- "turn on daily check-ins" (the reply says exactly what it is), "stop the check-ins", "pause the check-ins", "resume the check-ins",
  "check in at 7 pm", "check in with me" (right now).
- When asked: "not now" quiets it for the next two days. A question she ignores three days in a row makes it go quiet for two weeks by itself.
- Her answer: short or neutral gets a "Thanks for telling me." A longer one gets "Would you like me to keep a note of that in your private
  journal? I would save exactly what you said." Only her yes saves it. If it sounds heavy, it offers to just listen, and only starts if she says yes.

## What is kept
- The check-in settings file `data/checkin.json`: on/off, time, quiet-until dates, an ignored counter, the last day asked. Never anything she said.
- The journal = notes she says yes to, in the private reflection vault (the same place as the other private notes: separate from ordinary
  memories, second consent if the notebook is still off, optional encryption). Each note is her own words only, keyed by the date.
- She can ask "what have you saved" and delete everything ("delete my notes"). The check-in never reads notes aloud on its own.
- Nothing from it reaches the owner. There is no export and no screen for anyone else.

## Rules enforced by tests
Off until enabled; one question a day inside a 3 hour window; skipped, never repeated, if missed; waits for idle, awake, not quiet mode,
not after a safety signal, not on a low battery, not within 30 minutes of a daily discovery; a safety message in her answer gets the fixed
safety text and ends the check-in; no model calls; no stored words in settings; an unrelated question goes to ordinary conversation.

## Not built
- The phone app for reading the journal later. Reading needs her own login on a screen that only she controls; today she can only
  hear her notes aloud on request. That is the next piece, and it must stay hers alone.
- Whether she finds a daily question welcome, and the right time and tone, can only be learned by using it with her.
