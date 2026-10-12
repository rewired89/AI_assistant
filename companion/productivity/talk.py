"""Voice commands for notes and lists. Fully scripted: no language model sees these requests or writes the answers.

    "write this down: ..." / "remember this idea. ..."   one spoken note, saved as she said it
    "save a voice note" / "take a note"                   several turns; "pause", "continue", "done" or "cancel"
    "call it Robot enclosure"                             a title (while dictating, or right after saving)
    "read my last note" / "find the note about the robot" / "what did I write down yesterday"
    "delete that note"                                    asks first
    "make me a grocery list. We need eggs, rice and milk" / "add bananas to my grocery list" / "what's on my grocery list"
    "mark eggs as complete" / "uncheck eggs"              only an explicit command completes anything

Honesty: it says "saved" only after the store reported success, and says plainly when it could not save. Audio is not recorded yet,
so a note is text from the speech recognizer; if the store fails the draft stays in memory and she can say "done" again to retry."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo

from .store import ProductivityStore, StoreError, light_cleanup, suggest_title, tokens

_I = re.I | re.S
DICT_IDLE_S = 15 * 60.0
CONFIRM_S = 90.0
YES = {"yes", "yeah", "yep", "sure", "ok", "okay", "please", "yup", "confirm", "do"}
NO = {"no", "nope", "don't", "dont", "not", "cancel", "stop", "never", "wait", "nah"}
ORD = {"first": 0, "1st": 0, "one": 0, "second": 1, "2nd": 1, "two": 1, "third": 2, "3rd": 2, "three": 2, "fourth": 3, "four": 3, "last": -1}

PRE = r"^\W*(?:(?:hey |hi |ok |okay )?nine ?tails[,.!]?\s*)?(?:please )?"
ONESHOT = re.compile(PRE + r"(?P<phrase>(?:write|jot|note|put|type) (?:this|that|it) down|take (?:a )?(?:voice )?(?:note|memo)|save (?:a )?(?:voice )?(?:note|memo)|"
                     r"(?:start|begin|record|make|new) (?:a )?(?:voice )?(?:note|memo)|note to self|remember this idea|save this idea|remember this note)\b[\s:,.\-]*(?P<body>.*)$", _I)
DONE = re.compile(r"^\W*(?:ok(?:ay)?,? )?(?:done|i'?m done|that'?s (?:all|it)|finished|i'?m finished|save (?:it|that|the note|this)|end (?:the )?note|stop (?:the )?note|stop recording)\W*$", _I)
PAUSE = re.compile(r"^\W*(?:pause|hold on|wait a (?:sec|second|moment))\W*$", _I)
RESUME = re.compile(r"^\W*(?:continue|resume|go on|go ahead|keep going|i'?m back)\W*$", _I)
CANCEL = re.compile(r"^\W*(?:cancel|discard(?: it| that| the note)?|never ?mind|throw (?:it|that) away|don'?t save(?: it| that)?)\W*$", _I)
TITLE = re.compile(r"^\W*(?:call (?:it|this note|the note|that)|title (?:it|this)|name (?:it|this note)|rename (?:it|that|this|the last note|my last note))(?: to| as)? (?P<t>.+?)\W*$", _I)
CLEAN_ON = re.compile(r"\b(?:clean up|tidy up|lightly clean)\b.*\b(?:dictation|dictated notes|my notes|what i say)\b|\bclean up my dictation\b", _I)
VERBATIM = re.compile(r"\b(?:word for word|verbatim|exactly as i say|stop (?:cleaning|tidying) up)\b.*\b(?:dictation|notes?|what i say)\b|\bkeep my dictation (?:word for word|as is|exactly)\b", _I)
READ_LAST = re.compile(r"\b(?:read|play)(?: me| back)?(?: out)?(?: my| the)? (?:last|latest|most recent|previous) (?:note|memo)\b|\bread (?:that|it) (?:back|again|out)\b", _I)
FIND = re.compile(r"\b(?:find|search(?: for)?|look(?: up| for)|show me|read(?: me)?)\b.*\b(?:notes?|memos?)\b.*?\b(?:about|on|with|regarding|called|titled|named)\b\s*(?P<q>.+)$", _I)
WHAT_WROTE = re.compile(r"\bwhat (?:did|have) i (?:write|wrote|note|noted|jot|jotted|save|saved)(?: down)?\b(?P<when>.*)$", _I)
DELETE_NOTE = re.compile(r"\b(?:delete|remove|erase) (?:that|this|the last|my last|the latest) (?:note|memo)\b", _I)
CREATE_LIST = re.compile(r"\b(?:make|create|start|set up) (?:me )?(?:a |an |a new )?(?:(?P<name>[\w'&\- ]{2,40}?) list|list (?:for|called|named) (?P<name2>[\w'&\- ]{2,40}?))(?P<rest>(?:[.:,;]| and | with | we | i | for ).*)?\W*$", _I)
ADD_ITEMS = re.compile(r"\badd (?P<items>.+?) (?:to|on|onto) (?:my |the |our )?(?P<name>[\w'&\- ]*?)\s*list\b", _I)
LIST_READ = re.compile(r"\b(?:what'?s|what is|what are|what do i have) (?:on|in) (?:my|the|our) (?P<name>[\w'&\- ]*?)\s*(?:list|lists)\b|\bread(?: me)?(?: out)? (?:my|the|our) (?P<name2>[\w'&\- ]*?)\s*list\b", _I)
LISTS_OF_LISTS = re.compile(r"\bwhat lists do i have\b|\b(?:show|read) (?:me )?my lists\b", _I)
COMPLETE = re.compile(PRE + r"(?:mark|check off|tick off|cross off|complete|finish) (?P<item>.+?)(?: (?:off|as (?:complete|completed|done|finished)|complete|completed|done))?(?: (?:on|in|from) (?:my |the |our )?(?P<list>[\w'&\- ]+?) list)?\W*$", _I)
UNCHECK = re.compile(PRE + r"(?:uncheck|unmark|restore|reopen|put back) (?P<item>.+?)(?: (?:on|in|from) (?:my |the |our )?(?P<list>[\w'&\- ]+?) list)?\W*$", _I)
REMOVE_ITEM = re.compile(PRE + r"(?:remove|delete|take) (?P<item>.+?) (?:off|from|out of) (?:my |the |our )?(?P<list>[\w'&\- ]*?)\s*list\W*$", _I)
REMIND = re.compile(r"\bremind me\b", _I)
LEAD_IN = re.compile(r"^\W*(?:we|i|you)?\s*(?:need|needs|want|have to|should get|should buy)(?: to (?:get|buy|pick up))?\s+|^\W*(?:with|including|containing|add|put)\s+|^\W*:\s*", _I)


@dataclass
class NotesReply:
    text: str
    style: str = "calm"


def _yes_no(text: str) -> bool | None:
    w = set(re.findall(r"[a-z']+", text.lower()))
    y, n = bool(w & YES), bool(w & NO)
    return True if y and not n else False if n and not y else None


def split_items(text: str) -> list[str]:
    t = LEAD_IN.sub("", text.strip().strip(".!?"))
    parts = re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+|\s*;\s*|\s*&\s*", t)
    out = []
    for p in parts:
        p = re.sub(r"^\W*(?:some|a few|a bit of)\s+", "", p.strip(" .,!?"), flags=re.I).strip()
        if p and p.lower() not in {x.lower() for x in out}:
            out.append(p[:100])
    return out[:30]


def _pretty(name: str) -> str:
    n = re.sub(r"\s+list$", "", name.strip(), flags=re.I)
    n = re.sub(r"^(?:my|the|our|a|an)\s+", "", n, flags=re.I).strip()
    return (n[:1].upper() + n[1:]) if n else ""


class NotesTalk:
    def __init__(self, store: ProductivityStore, clock=time.time, tz: tzinfo = timezone.utc, phone_hint: bool = False,
                 reminders_enabled: bool = False):
        self.store, self.clock, self.tz, self.phone_hint = store, clock, tz, phone_hint
        self.reminders_enabled = reminders_enabled                 # when reminders exist, RemindTalk handles "remind me"
        self._dict: dict | None = None
        self._pend: dict | None = None
        self._last_id: str | None = None

    # ---- housekeeping ------------------------------------------------------------------------------
    def cancel(self) -> None:
        """A safety reply took over. A draft that was not saved is NOT saved (and was never claimed to be)."""
        self._dict = None
        self._pend = None

    @property
    def dictating(self) -> bool:
        return self._dict is not None

    def _phone(self) -> str:
        return " You can review it on your phone." if self.phone_hint else ""

    def _cleaning(self) -> bool:
        return self.store.setting("dictation_cleanup", "off") == "on"

    # ---- entry point -----------------------------------------------------------------------------
    def handle(self, utterance: str) -> NotesReply | None:
        now = self.clock()
        t = utterance.strip()
        if not t:
            return None
        if self._dict and now - self._dict["t"] > DICT_IDLE_S:
            self._dict = None                                    # nothing was saved and nothing was claimed
        if self._pend and now - self._pend["t"] > CONFIRM_S:
            self._pend = None
        if self._dict is not None:
            return self._dictating(t, now)
        pend, self._pend = self._pend, None
        if pend:
            r = self._answer_pending(pend, t)
            if r is not None:
                return r
        m = ONESHOT.search(t)
        if m:
            return self._start(m, now)
        mm = TITLE.match(t) if self._last_id else None
        if mm:
            return self._retitle(mm)
        if CLEAN_ON.search(t):
            return self._set_cleanup(True)
        if VERBATIM.search(t):
            return self._set_cleanup(False)
        if READ_LAST.search(t):
            return self._read_last()
        if DELETE_NOTE.search(t):
            return self._ask_delete(now)
        if LISTS_OF_LISTS.search(t):
            return self._lists_of_lists()
        mm = FIND.search(t)
        if mm:
            return self._find(mm.group("q"), None, now)
        mm = WHAT_WROTE.search(t)
        if mm:
            return self._find("", mm.group("when"), now)
        mm = CREATE_LIST.search(t)
        if mm:
            return self._create_list(mm)
        mm = ADD_ITEMS.search(t)
        if mm:
            return self._add_items(mm.group("items"), mm.group("name"), now)
        mm = LIST_READ.search(t)
        if mm:
            return self._read_list(mm.group("name") or mm.group("name2") or "", now)
        mm = REMOVE_ITEM.match(t)
        if mm:
            return self._item_action("remove", mm.group("item"), mm.group("list"), now)
        mm = UNCHECK.match(t)
        if mm:
            return self._item_action("uncheck", mm.group("item"), mm.group("list"), now)
        mm = COMPLETE.match(t)
        if mm:
            return self._item_action("complete", mm.group("item"), mm.group("list"), now, explicit=bool(mm.group("list")) or bool(re.search(r"\b(?:complete|completed|done|finished|off)\b", t, _I)))
        if REMIND.search(t) and not self.reminders_enabled:
            return NotesReply("I can't set reminders yet, so I haven't saved that. I can add it to a list if you tell me which one.")
        return None

    # ---- dictation -----------------------------------------------------------------------------
    def _start(self, m: re.Match, now: float) -> NotesReply:
        body = m.group("body").strip()
        phrase = m.group("phrase").lower()
        multi = bool(re.search(r"voice|take|record|start|begin|new|make|memo|save (?:a )?note", phrase)) and "down" not in phrase
        kind = "voice" if multi else "dictated"
        tags = ["idea"] if "idea" in phrase else []
        if body:
            return self._save(kind if multi else "dictated", [body], tags, None, now)
        self._dict = {"t": now, "parts": [], "paused": False, "multi": multi, "kind": kind, "tags": tags, "title": None}
        if multi:
            return NotesReply("Okay, I'm listening. Say done when you're finished, pause to take a break, or cancel to throw it away.")
        return NotesReply("Okay, go ahead.")

    def _dictating(self, t: str, now: float) -> NotesReply:
        d = self._dict
        d["t"] = now
        if CANCEL.match(t):
            self._dict = None
            return NotesReply("Okay, I threw that away. Nothing was saved.")
        tm = TITLE.match(t)
        if tm:
            d["title"] = tm.group("t").strip()[:100]
            return NotesReply(f"Okay, I'll call it {d['title']}.")
        if d["multi"] and DONE.match(t):
            return self._finish(now)
        if d["multi"] and PAUSE.match(t):
            d["paused"] = True
            return NotesReply("Paused. Say continue when you're ready.")
        if d["multi"] and d["paused"]:
            if RESUME.match(t):
                d["paused"] = False
                return NotesReply("Okay, go on.")
            return NotesReply("I'm paused. Say continue to carry on, or done to save what we have.")
        d["parts"].append(t)
        if not d["multi"]:
            return self._finish(now)                              # "write this down" ... one utterance
        return NotesReply("Got it.", "calm")

    def _finish(self, now: float) -> NotesReply:
        d = self._dict
        if not d["parts"]:
            self._dict = None
            return NotesReply("There was nothing to save, so I didn't make a note.")
        return self._save(d["kind"], d["parts"], d["tags"], d["title"], now)

    def _save(self, kind: str, parts: list[str], tags, title: str | None, now: float) -> NotesReply:
        cleaning = self._cleaning()
        body = " ".join(p.strip() for p in parts)
        if cleaning:
            body = light_cleanup(body)
        try:
            note = self.store.add_note(body, kind=kind, title=title, tags=tags, source="voice", cleaned=cleaning, transcript_state="done")
        except StoreError as e:
            if self._dict is None:
                self._dict = {"t": now, "parts": list(parts), "paused": False, "multi": True, "kind": kind, "tags": tags, "title": title}
            return NotesReply(f"I couldn't save that note, so it isn't saved yet. {str(e)[:120]}. Say done to try again, or cancel to throw it away.")
        except Exception:
            if self._dict is None:
                self._dict = {"t": now, "parts": list(parts), "paused": False, "multi": True, "kind": kind, "tags": tags, "title": title}
            return NotesReply("I couldn't save that note, so it isn't saved yet. Say done to try again, or cancel to throw it away.")
        self._dict = None
        self._last_id = note["id"]
        what = "idea" if "idea" in tags else "note"
        return NotesReply(f"Got it! I've saved your {what}, called {note['title']}.{self._phone()}", "happy")

    def _retitle(self, m: re.Match) -> NotesReply | None:
        try:
            n = self.store.update_note(self._last_id, title=m.group("t").strip())
        except StoreError:
            return NotesReply("I couldn't rename that note.")
        return NotesReply(f"Okay, it's called {n['title']} now.")

    def _set_cleanup(self, on: bool) -> NotesReply:
        self.store.set_setting("dictation_cleanup", "on" if on else "off")
        if on:
            return NotesReply("Okay, I'll lightly tidy your dictation: spacing, capital letters and a full stop. I won't change your words.")
        return NotesReply("Okay, I'll keep your dictation word for word.")

    # ---- reading, finding, deleting ---------------------------------------------------------------------
    def _speak_note(self, n: dict) -> NotesReply:
        self._last_id = n["id"]
        body = n["body"]
        more = ""
        if len(body) > 1500:
            body, more = body[:1500].rsplit(" ", 1)[0], " That's the first part. The rest is on your phone." if self.phone_hint else " That's the first part."
        return NotesReply(f"{n['title']}. {body}{more}")

    def _read_last(self) -> NotesReply:
        n = self.store.last_note()
        if n is None:
            return NotesReply("You don't have any notes yet.")
        return self._speak_note(n)

    def _when(self, text: str) -> tuple[float | None, float | None, str]:
        low = text.lower()
        today = datetime.fromtimestamp(self.clock(), self.tz).replace(hour=0, minute=0, second=0, microsecond=0)
        if "yesterday" in low:
            return (today - timedelta(days=1)).timestamp(), today.timestamp(), "yesterday"
        if "today" in low or "this morning" in low:
            return today.timestamp(), (today + timedelta(days=1)).timestamp(), "today"
        if "last week" in low:
            return (today - timedelta(days=7)).timestamp(), None, "in the last week"
        if "this week" in low:
            return (today - timedelta(days=today.weekday())).timestamp(), None, "this week"
        return None, None, ""

    def _find(self, query: str, when: str | None, now: float) -> NotesReply:
        since = until = None
        label = ""
        if when is not None:
            since, until, label = self._when(when)
            query = ""
            if since is None:
                since = (datetime.fromtimestamp(now, self.tz) - timedelta(days=7)).timestamp()
                label = "recently"
        found = self.store.notes(query, since=since, until=until, limit=6)
        if not found:
            return NotesReply("I couldn't find a note about that." if query else f"You didn't write anything down {label}.")
        if len(found) == 1:
            return self._speak_note(found[0])
        names = ", ".join(f"{i + 1}: {n['title']}" for i, n in enumerate(found[:4]))
        self._pend = {"t": now, "action": "choose", "ids": [n["id"] for n in found[:4]]}
        return NotesReply(f"I found {len(found)} notes. {names}. Which one would you like? Say first, second, or part of the title.")

    def _ask_delete(self, now: float) -> NotesReply:
        n = self.store.get_note(self._last_id) if self._last_id else None
        if n is None:
            return NotesReply("Which note? Ask me to read or find it first, then tell me to delete it.")
        self._pend = {"t": now, "action": "delete", "id": n["id"]}
        return NotesReply(f"Do you want me to delete the note called {n['title']}? That can't be undone. Say yes to confirm.")

    def _answer_pending(self, p: dict, t: str) -> NotesReply | None:
        a = p["action"]
        if a == "delete":
            y = _yes_no(t)
            if y is None:
                return None
            if not y:
                return NotesReply("Okay, I kept it.")
            ok = False
            try:
                ok = self.store.delete_note(p["id"])
            except Exception:
                ok = False
            if not ok:
                return NotesReply("I couldn't delete that note, so it is still there.")
            if self._last_id == p["id"]:
                self._last_id = None
            return NotesReply("Done. I deleted that note.")
        if a == "choose":
            ids = p["ids"]
            words = re.findall(r"[a-z0-9']+", t.lower())
            idx = next((ORD[w] for w in words if w in ORD), None)
            if idx is not None and -len(ids) <= idx < len(ids):
                n = self.store.get_note(ids[idx])
                return self._speak_note(n) if n else None
            q = set(tokens(t))
            best = [n for n in (self.store.get_note(i) for i in ids) if n and q and q & set(tokens(n["title"]))]
            if len(best) == 1:
                return self._speak_note(best[0])
            return None
        if a == "which_list":
            return self._resume_list_choice(p, t)
        if a == "make_list":
            y = _yes_no(t)
            if y is None:
                return None
            if not y:
                return NotesReply("Okay, I didn't add anything.")
            return self._create_named(p["name"], p["items"])
        if a == "which_item":
            return self._resume_item_choice(p, t)
        return None

    # ---- lists ------------------------------------------------------------------------------------------
    def _lists_of_lists(self) -> NotesReply:
        ls = self.store.lists()
        if not ls:
            return NotesReply("You don't have any lists yet.")
        return NotesReply("You have " + ", ".join(f"{l['name']} with {l['total'] - l['done']} to do" for l in ls[:6]) + ".")

    def _create_list(self, m: re.Match) -> NotesReply | None:
        name = _pretty(m.group("name") or m.group("name2") or "")
        rest = (m.group("rest") or "").strip()
        if not name:
            return None
        items = split_items(rest) if rest and not re.match(r"^\W*(?:for|and)\b", rest, _I) else []
        return self._create_named(name, items)

    def _create_named(self, name: str, items: list[str]) -> NotesReply:
        existing = self.store.find_list(name, exact=True)
        try:
            if existing:
                lst = existing[0]
                added = 0
                for it in items:
                    self.store.add_item(lst["id"], it)
                    added += 1
                return NotesReply(f"You already have a {lst['name']} list. I added {added} item{'s' if added != 1 else ''} to it." if items else f"You already have a {lst['name']} list.")
            lst = self.store.create_list(name, items)
        except StoreError as e:
            return NotesReply(f"I couldn't make that list. {str(e)[:100]}.")
        n = lst["total"]
        return NotesReply(f"Your {lst['name'].lower()} list is ready! I've added {n} item{'s' if n != 1 else ''}." if n else f"Your {lst['name'].lower()} list is ready. It's empty for now.", "happy")

    def _pick_list(self, name: str, now: float, then: dict) -> tuple[dict | None, NotesReply | None]:
        """The one list she means, or a clarifying question (nothing is modified until it is clear)."""
        name = _pretty(name)
        ls = self.store.find_list(name) if name else self.store.lists()
        if len(ls) == 1:
            return ls[0], None
        if not ls:
            if name:
                self._pend = {"t": now, "action": "make_list", "name": name, "items": then.get("items", [])} if then.get("kind") == "add" else None
                if then.get("kind") == "add":
                    return None, NotesReply(f"I don't have a list called {name.lower()}. Want me to make one?")
                return None, NotesReply(f"I don't have a list called {name.lower()}.")
            return None, NotesReply("You don't have any lists yet. You can say make me a grocery list.")
        self._pend = {"t": now, "action": "which_list", "ids": [l["id"] for l in ls[:4]], "then": then}
        return None, NotesReply("Which list: " + ", or ".join(l["name"] for l in ls[:4]) + "?")

    def _resume_list_choice(self, p: dict, t: str) -> NotesReply | None:
        ids = p["ids"]
        words = re.findall(r"[a-z0-9']+", t.lower())
        idx = next((ORD[w] for w in words if w in ORD), None)
        chosen = None
        if idx is not None and -len(ids) <= idx < len(ids):
            chosen = self.store.get_list(ids[idx])
        else:
            q = set(tokens(t))
            hits = [l for l in (self.store.get_list(i) for i in ids) if l and q and q <= set(tokens(l["name"]))]
            if len(hits) == 1:
                chosen = hits[0]
        if chosen is None:
            return None
        then = p["then"]
        if then["kind"] == "add":
            return self._do_add(chosen, then["items"])
        if then["kind"] == "read":
            return self._speak_list(chosen)
        return self._item_in_list(then["verb"], then["item"], chosen, self.clock())

    def _add_items(self, items_text: str, name: str, now: float) -> NotesReply:
        items = split_items(items_text)
        if not items:
            return NotesReply("What would you like me to add?")
        lst, ask = self._pick_list(name, now, {"kind": "add", "items": items})
        return ask if lst is None else self._do_add(lst, items)

    def _do_add(self, lst: dict, items: list[str]) -> NotesReply:
        added = 0
        try:
            for it in items:
                self.store.add_item(lst["id"], it)
                added += 1
        except StoreError as e:
            return NotesReply(f"I added {added} but couldn't add the rest. {str(e)[:100]}.")
        return NotesReply(f"Added {added} item{'s' if added != 1 else ''} to your {lst['name'].lower()} list.")

    def _read_list(self, name: str, now: float) -> NotesReply:
        lst, ask = self._pick_list(name, now, {"kind": "read"})
        return ask if lst is None else self._speak_list(lst)

    def _speak_list(self, lst: dict) -> NotesReply:
        full = self.store.get_list(lst["id"])
        todo = [i["text"] for i in full["items"] if not i["done"]]
        done = full["done"]
        if not todo:
            return NotesReply(f"Your {full['name'].lower()} list has nothing left to do." + (f" {done} done." if done else ""))
        shown = todo[:12]
        more = f", and {len(todo) - 12} more" if len(todo) > 12 else ""
        return NotesReply(f"On your {full['name'].lower()} list: " + ", ".join(shown) + more + ".")

    def _item_action(self, verb: str, item: str, listname: str | None, now: float, explicit: bool = True) -> NotesReply | None:
        if not self.store.lists():
            return None                                           # no lists at all: "mark my words" is ordinary speech
        if listname:
            lst, ask = self._pick_list(listname, now, {"kind": "item", "verb": verb, "item": item})
            if lst is None:
                return ask
            return self._item_in_list(verb, item, lst, now)
        return self._item_in_list(verb, item, None, now, explicit=explicit)

    def _item_in_list(self, verb: str, item: str, lst: dict | None, now: float, explicit: bool = True) -> NotesReply | None:
        only_open = verb == "complete"
        hits = self.store.find_items(item, lst["id"] if lst else None, only_open=only_open)
        if verb == "uncheck":
            hits = [h for h in hits if h["done"]]
        if not hits:
            return NotesReply(f"I couldn't find {item.strip()} on your lists, so I didn't change anything.") if (lst or explicit) else None
        if len(hits) > 1:
            self._pend = {"t": now, "action": "which_item", "ids": [h["id"] for h in hits[:4]], "verb": verb}
            return NotesReply("Which one: " + ", or ".join(f"{h['text']} on your {h['list_name'].lower()} list" for h in hits[:4]) + "?")
        return self._apply(verb, hits[0])

    def _apply(self, verb: str, h: dict) -> NotesReply:
        try:
            if verb == "remove":
                self.store.delete_item(h["id"])
                return NotesReply(f"Removed {h['text']} from your {h['list_name'].lower()} list.")
            self.store.update_item(h["id"], done=(verb == "complete"))
        except StoreError:
            return NotesReply("I couldn't change that item, so nothing changed.")
        return NotesReply(f"Marked {h['text']} as done." if verb == "complete" else f"Put {h['text']} back on your {h['list_name'].lower()} list.")

    def _resume_item_choice(self, p: dict, t: str) -> NotesReply | None:
        ids = p["ids"]
        words = re.findall(r"[a-z0-9']+", t.lower())
        idx = next((ORD[w] for w in words if w in ORD), None)
        items = [self.store.get_item(i) for i in ids]
        pick = None
        if idx is not None and -len(ids) <= idx < len(ids):
            pick = items[idx]
        else:
            q = set(tokens(t))
            lists = {l["id"]: l["name"] for l in self.store.lists()}
            hits = [i for i in items if i and q and q & set(tokens(lists.get(i["list_id"], "")))]
            if len(hits) == 1:
                pick = hits[0]
        if not pick:
            return None
        h = {**pick, "list_name": next((l["name"] for l in self.store.lists() if l["id"] == pick["list_id"]), "")}
        return self._apply(p["verb"], h)
