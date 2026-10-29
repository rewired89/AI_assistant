# Psychological Growth Engine

For a professional to review: `python -m tools.review_packet` builds `docs/clinician-review/PACKET.html` (print to PDF) with every sentence she can hear, plus `responses.csv` to answer item by item. A test keeps it in step with the code.

Status: implemented and tested against synthetic scenarios and scripted/fake models (`companion/psychology`, `tests/psychology`). **Not reviewed by a licensed clinician and not used with a real person.** Get a psychologist or counselor to read the scripts in `exercises/library.yaml`, `safety/screen.py` and `conflict_navigation/guide.py` before she uses it for a real conflict.

## What it is and is not
Education and reflection support: short guided exercises, a listening mode, preparation for hard conversations, break-and-return planning, and goals she chooses. It does not diagnose, label people (her or anyone else), treat, score, rank, decide who is right, advise staying in or leaving a relationship, or comment on her therapy. It is separate from personality and tamagotchi code: reflection turns earn no growth, no play, no memory, and are not kept in chat history.

## Flow of a turn (`ConversationEngine.respond`)
1. **SafetyGate** (always, any mode, even if the feature is off): regex screen for self-harm, abuse/coercion, intent to hurt others, reality-testing/health signals. A hit returns fixed text with human resources (988 / 911 / domestic-violence hotline for region US; generic text for unknown regions), calls no model, saves nothing, and holds a short scripted follow-up (two turns, ten minutes). For an hour afterwards no exercise is offered or started (she can still ask for slow breathing).
2. **PsychologyEngine** (only if the character allows it, it is not paused/off, and she asked or accepted an offer). Returns None otherwise and normal conversation continues.
3. Ordinary chat: model plus the base output filter.

## Consent and control
- `psychological_growth_features`: `off` never; `opt_in` only when she explicitly asks (breathing, "help me with this fight", "set a goal", "I just want to vent"...); `on` may offer at most once a day, never in quiet mode or after a safety signal, and a "no thanks" silences offers for 3 days. Every offer needs a yes.
- First use: plain-language disclosure (coaching/education not therapy, no diagnosis, private, stop anytime, can be turned off) and a yes. Revocable.
- Voice: "pause / turn off / turn on the reflection helper". Voice cannot turn on what settings switched off. Any flow: "stop", "skip", "never mind".
- Notes: nothing is saved without a separate yes; saving needs the private notebook (off by default, enabled by her yes). Notes are her own words only, kept 60 days by default, readable aloud only after a "nobody else is listening" confirmation, deletable by voice with confirmation. Reminders store a time and the generic label "a plan you made", never a topic. Settings file contains no content (tested).
- No export path to anyone else; the vault is bound to one user id (opening it as another user fails).

## Exercises (`exercises/library.yaml`)
naming_emotions, facts_vs_interpretations, reflective_listening_practice, conflict_map, prepare_difficult_conversation, values_clarification, self_compassion_break, break_and_return_plan, accountability_steps, repair_attempt_reflection, slow_breathing, grounding_54321, conflict_navigation (guide), goal_review (growth plan). Each has the required fields (id, name, skill, evidence basis, when to offer / not offer, duration, instructions, reflection questions, privacy behavior, success criteria, limitations) and cites entries in the evidence register (`docs/psychology-evidence.md`, generated). Success criteria are things she can see for herself, never agreement with the robot or with the person describing a conflict (a test enforces it).
Runner rules: skip any step, stop any time (only a short command counts, never words inside an answer), one retry on unclear answers then move on, very high intensity (9-10) ends the questions and offers breathing, no scoring.

## Conflict walk-through
Stages NOTICE, IDENTIFY EMOTIONS, UNDERSTAND EACH PERSPECTIVE, SEPARATE FACTS FROM ASSUMPTIONS, NEEDS AND BOUNDARIES, CONTINUE OR BREAK, NEXT STEP, then a later REVISIT AND REPAIR. She picks quick or full, skips anything, and can ask for a break at any point; strong overwhelm phrases pause the questions and offer breathing or a break plan. "Settle things soon", "need space first" and "both" each get the matching questions from the spec, with no assumption about which partner she is. The other person's view is always a guess she can check by asking. Break plans require a specific return time and teach both sides (taking space is healthy; vanishing or pushing someone who asked for space both damage trust). A reminder can bring her back, content-free.

## What the model may and may not do here
Only listening mode uses a model, only if it is marked `is_local`, and its input is wrapped as data. Output must pass `safety/validator.py`: no verdicts about people, relationship directives, therapy meddling, mind-reading, global accusations ("you always"), promises to heal, isolation framing, sycophancy ("you're absolutely right"), certainty about feelings without hedging, guilt/dependency, diagnoses. Two blocked attempts fall back to short scripted backchannels. Everything else is scripted, so it cannot drift.

## Known limits and open risks
- **Regex screens miss indirect or unusual wording and cannot read context**; they also have false positives. Emphasis is on missing nothing obvious; tests cover idioms. This is not a clinical risk assessment.
- **Region and numbers**: defaults to `US`; she was never asked. Hotline numbers were checked only against secondary summaries (one source linked 1-800-799-7233 to a different service). Confirm at 988lifeline.org and thehotline.org and set her region.
- **Evidence is thin for several techniques** (see the register): grounding, NVC, and the Gottman 20-minute figure are rules of thumb. Several sources were "not checked".
- **Third-party speaker problem**: the robot cannot tell who is speaking. If someone else describes "her" behavior, flows still address whoever is talking and say they only know one side. There is no mode where one person can analyze another. Consider voice enrollment later.
- **Your position**: you built this and have a stake in a specific disagreement. Nothing from her reflections reaches you; there is no report, summary or export path. Do not use it to prepare "her" side, do not ask her what she said to it, and treat any reminder you happen to hear as hers.
- Not covered: pauses between breathing lines are produced as hints (`PsyReply.pauses`) but the voice pipeline does not yet insert silence; real speech timing; real-model behavior in listening mode (only scripted fakes); non-English input; crisis handling when STT garbles words; sessions are lost on restart by design.
