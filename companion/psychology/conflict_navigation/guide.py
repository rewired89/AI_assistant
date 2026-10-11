"""The conflict-navigation stages as an exercise the generic runner can step through.

NOTICE -> IDENTIFY EMOTIONS -> UNDERSTAND EACH PERSPECTIVE -> SEPARATE FACTS FROM ASSUMPTIONS ->
IDENTIFY NEEDS AND BOUNDARIES -> CHOOSE: CONTINUE OR TAKE A BREAK -> AGREE ON A NEXT STEP -> REVISIT AND REPAIR.
She picks the depth (quick or full), can skip any step, and can ask for a break at any point. The guide hears one
side only, never says who is right, and treats the other person's view as a guess she can check by asking.
Overwhelm (a high intensity rating, or her saying so) ends the questions and offers calming or a break first."""
from __future__ import annotations

import copy
import re

# Strong phrases only. The plain word "overwhelmed" is a legitimate answer ("I get overwhelmed"), so it must not trigger this.
OVERWHELMED = re.compile(r"\b(can'?t (breathe|think|do this)|i'?m shaking|panick?ing|having a panic|this is too much|losing it|"
                         r"about to (explode|cry|scream))\b", re.I)
WANTS_BREAK = re.compile(r"\b(need|want|take|ask for) (a )?(break|space|time out|timeout|pause)\b|\bgive (me|us) (some )?space\b", re.I)
CONFLICT_OPENERS = re.compile(r"\b(we|i|me and \w+|\w+ and i) (had|got into|are in|were in) (a )?(fight|argument|disagreement|"
                              r"conflict|row)\b|\b(fought|argued|fighting|arguing) with\b|\b(fight|argument|disagreement) with (my|our)\b", re.I)

EMOTIONS = ["frustrated", "hurt", "disappointed", "anxious", "overwhelmed", "sad", "angry", "lonely"]
SETTLE_SPACE = {"settle": "wanting to settle things soon", "space": "needing space first", "both": "both, at different times"}


def _ask(id_, prompt, **kw):
    return {"id": id_, "kind": "ask_text", "prompt": prompt, **kw}


def _steps(depth: str) -> list[dict]:
    notice = _ask("notice", "What's going on? Tell me in a sentence or two.", recap="What's going on")
    emotion = {"id": "emotion", "kind": "ask_choice", "options": EMOTIONS, "allow_other": True, "recap": "How I feel",
               "prompt": "Which feeling is closest right now? Frustrated, hurt, disappointed, anxious, overwhelmed, sad, angry, "
                         "lonely, or something else. I might be wrong, so correct me.", "indicator": "named_feeling"}
    intensity = {"id": "intensity", "kind": "ask_scale", "high_threshold": 9, "prompt": "How strong is it, from zero to ten?"}
    need = _ask("need", "What do you need in this?", recap="What I need")
    choose = {"id": "choose", "kind": "ask_choice", "options": ["continue", "break"], "route": {"break": "break_and_return_plan"},
              "route_text": "Okay, let's plan the break so that it has a way back.", "allow_other": False,
              "prompt": "Do you want to keep going now, or take a break and come back to it? Say continue or break.",
              "indicator": "chose_continue_or_break"}
    step = _ask("next_step", "What is one small, realistic step you could take next?", recap="My next step", indicator="chose_next_step")
    intro = {"id": "intro", "kind": "say", "text": "I only know your side, and I wasn't there, so I won't say who is right. "
                                                   "We'll go at your pace, you can skip anything, and you can ask for a break at any time."}
    if depth == "quick":
        return [intro, notice, emotion, intensity, need, choose, step]
    when = lambda *vals: {"step": "pattern", "in": list(vals)}
    return [
        intro, notice, emotion, intensity,
        {"id": "pattern", "kind": "ask_choice", "options": ["settle", "space", "both"], "allow_other": False,
         "prompt": "In this situation, which sounds most like you: wanting to settle things soon, needing space first, "
                   "or both at different times? Say settle, space, or both."},
        _ask("space_overwhelm", "What part of this conversation became overwhelming?", when=when("space", "both"),
             recap="What became overwhelming"),
        _ask("space_safe", "What would help you feel safe enough to return to the topic?", when=when("space", "both"),
             recap="What would help me return"),
        _ask("settle_hard", "What makes it difficult to leave a problem unresolved until later?", when=when("settle", "both"),
             recap="Why waiting is hard"),
        _ask("settle_time", "Would you be willing to agree on a specific time to continue, so it doesn't just disappear?",
             when=when("settle", "both"), recap="A time I'd accept"),
        _ask("break_respect", "What would make a break feel respectful to both people?", recap="What makes a break respectful"),
        _ask("their_side", "What might their side be? Treat it as a guess, and the way to find out is to ask them.",
             recap="A guess at their side", indicator="considered_other_side"),
        _ask("facts", "What happened that a camera would have recorded, with no judgment?", recap="Observable facts"),
        _ask("assumptions", "What are you assuming about why it happened, and how could you check that?", recap="Assumptions to check",
             indicator="separated_facts_from_assumptions"),
        need,
        _ask("boundary", "What are you willing to do, and what are you not willing to do?", recap="My limits"),
        choose,
        step,
        _ask("each_person", "What is one thing each person could do differently next time?", optional=True, recap="Next time"),
    ]


def build_conflict_exercise(base: dict, depth: str) -> dict:
    if depth not in ("quick", "full"):
        raise ValueError(depth)
    ex = copy.deepcopy(base)
    ex["steps"] = _steps(depth)
    return ex


def build_revisit_exercise(base: dict) -> dict:
    ex = copy.deepcopy(base)
    ex["steps"] = [
        {"id": "intro", "kind": "say", "text": "It's the time you planned to come back to something. We'll take it gently and at your pace."},
        _ask("since", "What has happened since the break?", recap="Since the break"),
        _ask("helped", "Did the break help? What did you notice?", recap="What the break did"),
        _ask("missed", "If the planned time passed without the two of you talking, what could you say to ask for a new time kindly?",
             optional=True, recap="Asking for a new time"),
        _ask("say", "What do you want to say or ask now? Start from a gentle opening and speak for yourself.", recap="What I want to say"),
        _ask("acknowledge", "Is there anything you want to acknowledge or repair?", optional=True, recap="What to acknowledge"),
        _ask("follow", "How will you follow through on what you decide?", recap="My follow-through", indicator="made_followthrough_plan"),
    ]
    return ex
