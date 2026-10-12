# Learning from her (spec sections 11, 13, 14)

Learning here is **bounded, visible and consent-based**. It never edits the character, the system prompt or security rules.

## What can be learned, and how
| Kind | Mechanism | Needs her yes? |
|---|---|---|
| Facts and preferences she states ("my favorite color is green") | rule-based extraction + the model's `remember` tool, only when grounded in her words | no for plain statements; pending + ask when the model inferred it |
| Routines (usual time of day to chat, usual greeting) | `companion/learning/routines.py` | yes, always (stored as a PENDING inference until she confirms) |
| Personality adaptation | `Personality.adapt` exists with hard drift limits, but nothing calls it yet | n/a (not built) |

## Routine recognition: the rules (tested)
- Watches only her conversations with the robot, nothing else. Keeps a timestamp, the kind of event, the part of the day, and for greetings one word from a fixed list. Never what she said (a test checks a secret sentence never reaches the file). Evidence expires after 45 days.
- Not from one day: at least 4 different days within 21, covering at least half of her chat days (and at least 5 chat days in total). Greeting routine: 4 uses on 3 days and 60% of her greetings.
- A candidate becomes a pending memory (`goal_habit`, source `ai_inference`); pending memories are never used in conversation. The robot asks once ("I've noticed you often talk with me in the evening. Do you want me to remember that?"), at most once per 3 days per routine, never inside safety or private exercises. Yes confirms. No deletes it and silences that routine for 30 days. A confirmed routine is never overwritten by a new inference.
- She can ask "what routines have you noticed?", say "forget my routines" (asks first), or "stop noticing my routines" (turns it off and erases the evidence). Turning off the `goal_habit` memory category also stops it.

## Talking about memory by voice (`companion/learning/memory_talk.py`)
Scripted, no language model, never touches the private reflection vault.
"what do you remember about me?" lists up to four things and counts the rest; "forget my favorite color" finds it, asks, deletes on yes; "forget that" works right after something was saved; "forget everything" asks twice and clears only ordinary memory. A "yes" only counts within 90 s and is dropped if a safety reply or private exercise happens in between. "Don't forget to call mom" and "I forgot my keys" are not deletion requests.

## Limits
Time-of-day buckets use the configured timezone (a wrong timezone gives wrong routines). Routines are remembered but not yet used for behavior (for example, speaking proactively only at her usual time); that is the natural next step. Spoken memory lists can be overheard by anyone in the room. Pattern matching for "forget ..." phrases is regex-based and will miss unusual wording (it errs toward not deleting). No UI beyond voice and the owner's command-line tool, and the owner's tool cannot read her private reflections.
