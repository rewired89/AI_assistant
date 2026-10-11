"""The only things the language model may ask for. Everything here is a *request*: arguments are
validated, grounded in what the user actually said, and executed (or refused) by host code.
There is deliberately no tool for configuration, memory deletion, brightness, mic, firmware or raw protocol."""
from __future__ import annotations

import re
from dataclasses import dataclass

from protocol.schema_consts import ANIMATIONS

MAX_VALUE = 200
KEY_RE = re.compile(r"^[a-z0-9_]{1,40}$")
LLM_MEMORY_CATEGORIES = ("profile", "semantic", "goal_habit")    # never episodic/reflection
SLEEP_WORDS = re.compile(r"\b(go to sleep|go sleep|time (for you )?to sleep|sleep now|take a nap|nap now|have a nap|get some rest|"
                         r"save (your )?(battery|power)|power (down|off|save)|wake me|good ?night|"
                         r"turn (yourself )?off|shut ?down|go to bed|sleep (until|till)|sleep for)\b", re.I)

TOOL_SPECS = [
    {"name": "remember",
     "description": "Save one fact the user just told you about themselves. Only for things they actually said.",
     "parameters": {"type": "object", "required": ["category", "value"], "properties": {
         "category": {"type": "string", "enum": list(LLM_MEMORY_CATEGORIES)},
         "key": {"type": "string", "description": "short snake_case label, e.g. favorite_color"},
         "value": {"type": "string", "description": "the fact, in the user's words"}}}},
    {"name": "request_sleep",
     "description": "Only when the user asks you to sleep, nap or save battery. Give the wake-up time they said, if any.",
     "parameters": {"type": "object", "properties": {
         "hour": {"type": "integer", "description": "1-12 with meridiem, or 0-23 for 24-hour time"},
         "minute": {"type": "integer"},
         "meridiem": {"type": "string", "enum": ["am", "pm"]},
         "reason": {"type": "string", "enum": ["battery_saving", "user_request"]}}}},
    {"name": "show_emotion",
     "description": "Optional: a short animation to show after you finish speaking.",
     "parameters": {"type": "object", "required": ["name"], "properties": {
         "name": {"type": "string", "enum": sorted(ANIMATIONS - {"offline", "sleeping", "waking", "listening", "thinking", "talking"})}}}},
]


@dataclass
class Remember:
    category: str
    value: str
    key: str | None


@dataclass
class RequestSleep:
    hour: int | None
    minute: int
    meridiem: str | None
    reason: str


@dataclass
class ShowEmotion:
    name: str


def parse_tool_calls(calls, max_calls: int = 3) -> tuple[list, int]:
    """Returns (valid actions, number of rejected calls). Never raises on model output."""
    actions, rejected = [], 0
    for call in (calls or [])[:max_calls]:
        try:
            name, args = call["name"], call.get("arguments") or {}
            if not isinstance(args, dict):
                raise TypeError("arguments")
            if name == "remember":
                value = str(args["value"]).strip()
                key = args.get("key")
                if args["category"] not in LLM_MEMORY_CATEGORIES or not 0 < len(value) <= MAX_VALUE:
                    raise ValueError("remember")
                actions.append(Remember(args["category"], value, key if isinstance(key, str) and KEY_RE.match(key) else None))
            elif name == "request_sleep":
                h, m, mer = args.get("hour"), args.get("minute", 0), args.get("meridiem")
                if h is not None and (isinstance(h, bool) or not isinstance(h, int)):
                    raise ValueError("hour")
                if isinstance(m, bool) or not isinstance(m, int):
                    raise ValueError("minute")
                if mer not in (None, "am", "pm"):
                    raise ValueError("meridiem")
                reason = args.get("reason") if args.get("reason") in ("battery_saving", "user_request") else "user_request"
                actions.append(RequestSleep(h, m, mer, reason))
            elif name == "show_emotion":
                if args["name"] not in ANIMATIONS or args["name"] in ("offline", "sleeping", "waking", "listening", "thinking", "talking"):
                    raise ValueError("emotion")
                actions.append(ShowEmotion(args["name"]))
            else:
                raise ValueError("unknown tool")
        except (KeyError, TypeError, ValueError, AttributeError):
            rejected += 1
    rejected += max(0, len(calls or []) - max_calls)
    return actions, rejected
