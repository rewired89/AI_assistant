"""Builds the clinician review packet: every sentence the robot can say in a psychology context, with an ID, in one
printable document, plus a response sheet so a reviewer can answer item by item. Text is read straight from the code and
content files, so the packet cannot drift from what she would actually hear.

    python -m tools.review_packet                  # writes docs/clinician-review/PACKET.md, PACKET.html, responses.csv
    python -m tools.review_packet --check          # fails if the committed files are out of date

Open PACKET.html in a browser and print it to PDF to hand to a professional. Nothing here has been reviewed yet."""
from __future__ import annotations

import argparse
import ast
import csv
import datetime as dt
import html
import importlib
import io
import re
import sys
from pathlib import Path

from companion import checkin as ck
from companion.discoveries import library as dl
from companion.psychology import engine as pe
from companion.psychology.conflict_navigation import guide
from companion.psychology.exercises.library import load_library
from companion.psychology.growth_plans import plans
from companion.psychology.safety.gate import HOLD_SECONDS, HOLD_TURNS, QUIET_WINDOW
from companion.psychology.sources import load_sources

sc = importlib.import_module("companion.psychology.safety.screen")      # (the package re-exports a function with the same name)
val = importlib.import_module("companion.psychology.safety.validator")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "clinician-review"


class Doc:
    def __init__(self):
        self.blocks: list[tuple] = []
        self.items: list[tuple[str, str, str]] = []      # (id, where, text) for the response sheet

    def h(self, level, text): self.blocks.append(("h", level, text))
    def p(self, text): self.blocks.append(("p", text))
    def ul(self, rows): self.blocks.append(("ul", list(rows)))
    def code(self, text): self.blocks.append(("code", text))
    def table(self, head, rows): self.blocks.append(("table", head, [list(map(str, r)) for r in rows]))

    def item(self, id_, where, text):
        """A line the robot can say, with a review ID."""
        self.items.append((id_, where, text))
        self.blocks.append(("item", id_, where, text))


def _regexlike(s: str) -> bool:
    return bool(re.search(r"\\[bswd]|\(\?|\[\^|\^\\W", s))


def harvest(path: Path) -> list[tuple[int, str]]:
    """Longer plain sentences in a source file (not docstrings, not patterns): the scripted lines not held in a data file."""
    tree = ast.parse(path.read_text())
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
    out = {(n.lineno, n.value) for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
           and len(n.value) >= 25 and " " in n.value and not _regexlike(n.value) and not n.value.startswith(("http", "%"))}
    return sorted(out)


def build() -> Doc:
    d = Doc()
    d.h(1, "Nine Tails: reflection and safety scripts for clinical review")
    d.p("Status: NOT reviewed by any licensed professional. Nothing here has been used with a real person in real distress. "
        "This packet exists so a psychologist, counselor or other qualified reviewer can read everything the companion robot "
        "can say in a psychology-related situation and tell us what to keep, change or remove.")
    d.h(2, "1. What the product is, and what we ask of you")
    d.ul(["Nine Tails is a small desk companion robot (a gift for one person). It talks, keeps her notes and reminders, and has an optional "
          "'reflection helper' that offers short guided exercises, a listening mode, break-and-return planning and daily check-ins.",
          "It is positioned as coaching and education, NOT therapy. It must never diagnose, label people, say who is right, advise staying or leaving "
          "a relationship, or comment on someone's therapy.",
          "Almost everything is fixed, pre-written text (listed below word for word). The only model-written text is the listening mode, and it "
          "passes a rule filter. See section 5.",
          "WE ASK YOU to read each item and mark Keep, Revise (with wording) or Remove on the response sheet (responses.csv, same IDs). Please also "
          "answer the open questions in section 9. Disagreeing strongly with any design choice is exactly what we need."])
    d.h(2, "2. Design commitments")
    d.ul(["Opt-in and revocable: nothing starts without her yes; she can say stop, pause or turn the helper off at any time.",
          "Private by default: nothing is saved unless she says yes to a specific note, saved notes are her own words only, kept 60 days by default, deletable by voice.",
          "No scores, streaks, rankings or progress pressure. No shame, guilt or dependency language. No 'I miss you' or similar.",
          "Safety first: every utterance is screened before anything else (section 3). If the screen fires, only the fixed reply is spoken.",
          "She can skip any question. Very high distress (9 or 10 of 10) ends the questions and offers breathing.",
          "Nothing from her reflections reaches the person who gave her the robot."])

    # ------------------------------------------------------------------ safety
    d.h(2, "3. Safety screen and fixed safety replies")
    d.p(f"The screen runs on every utterance BEFORE any model. It is a list of patterns (appendix A), so it WILL miss indirect or unusual wording. "
        f"After a crisis or abuse hit the robot holds a short scripted follow-up for {HOLD_TURNS} more turns or {HOLD_SECONDS // 60} minutes, and for "
        f"{QUIET_WINDOW // 3600} hour no exercise is offered (slow breathing is still available on request). Region defaults to US until she confirms; "
        "numbers must be verified (open question).")
    for level in (sc.RiskLevel.CRISIS, sc.RiskLevel.ABUSE, sc.RiskLevel.VIOLENCE, sc.RiskLevel.SUPPORT):
        d.item(f"S-{level.value}", f"Safety reply, level '{level.value}' (US)", sc.safety_reply(sc.Risk(level), "US"))
    d.item("S-generic", "Safety reply, level 'crisis', region unknown", sc.safety_reply(sc.Risk(sc.RiskLevel.CRISIS), "GENERIC"))
    d.item("S-follow-unsafe", "Follow-up after crisis, she says she is NOT safe", sc.followup_reply("no", sc.RiskLevel.CRISIS, "US"))
    d.item("S-follow-safe", "Follow-up after crisis, she says she IS safe", sc.followup_reply("yes I am safe", sc.RiskLevel.CRISIS, "US"))
    d.item("S-follow-unclear", "Follow-up after crisis, unclear answer", sc.followup_reply("hmm", sc.RiskLevel.CRISIS, "US"))
    d.item("S-follow-abuse-unsafe", "Follow-up after abuse signal, she says NOT safe", sc.followup_reply("no", sc.RiskLevel.ABUSE, "US"))
    d.h(3, "Resources used (verify every number)")
    d.table(["Region", "Emergency", "Suicide crisis", "Domestic violence"],
            [(r, v["emergency"], v["suicide_crisis"], v["domestic_violence"]) for r, v in
             __import__("yaml").safe_load((ROOT / "companion/psychology/safety/resources.yaml").read_text())["regions"].items()])

    # ------------------------------------------------------------------ exercises
    d.h(2, "4. Guided exercises (all fixed text)")
    d.item("X-disclosure", "Disclosure before first use (she must say yes)", pe.DISCLOSURE)
    lib = load_library()
    src = load_sources()
    for eid, ex in lib.items():
        d.h(3, f"{ex['name']}  [{eid}]")
        d.ul([f"Skill: {ex['intended_skill']}", f"Offered when: {'; '.join(ex['when_to_offer'])}",
              f"NOT offered when: {'; '.join(ex['when_not_to_offer'])}", f"Duration: {ex['estimated_duration']}",
              f"Evidence basis: " + "; ".join(f"{s} ({src[s]['evidence_level']})" for s in ex["source_or_evidence_basis"]),
              f"Privacy: {ex['privacy_behavior']}", f"Limitations: {'; '.join(ex['limitations'])}",
              f"Success criteria (things she can see herself): {'; '.join(ex['success_criteria'])}"])
        steps = ex["steps"]
        if ex["kind"] == "conflict_guide":
            for depth in ("quick", "full"):
                for st in guide.build_conflict_exercise(ex, depth)["steps"]:
                    _step_item(d, f"{eid}.{depth}", st)
            for st in guide.build_revisit_exercise(ex)["steps"]:
                _step_item(d, f"{eid}.revisit", st)
        else:
            for st in steps or []:
                _step_item(d, eid, st)
        for i, q in enumerate(ex.get("reflection_questions", []), 1):
            d.item(f"X-{eid}.reflect{i}", f"{ex['name']}: closing reflection question", q)
    d.h(3, "Goals she chooses (growth plans)")
    d.p(f"At most {plans.MAX_ACTIVE} active goals. She chooses or writes her own; these are the suggestions offered. Check-in outcomes: "
        + ", ".join(plans.OUTCOMES) + ". No scoring.")
    for i, s in enumerate(plans.SUGGESTIONS, 1):
        d.item(f"G-{i}", "Suggested goal", "I want to " + s)

    # ------------------------------------------------------------------ listening
    d.h(2, "5. Listening mode (the only part where a model writes text)")
    d.p(f"She asks to vent ('can you just listen'). A local model replies in at most two short sentences, every reply passes the filter in appendix B, "
        f"two blocked attempts fall back to the fixed lines below. The check-in offering a short exercise happens every {pe.LISTEN_CHECK_EVERY} turns, "
        f"and the mode ends after {pe.LISTEN_MAX_TURNS} turns with a suggestion to share with someone she trusts.")
    d.code(pe.LISTEN_PROMPT + "<secret token>")
    for i, line in enumerate(pe.LISTEN_BACKCHANNELS, 1):
        d.item(f"L-{i}", "Fixed fallback line while listening", line)
    d.p("OPEN RISK: a language model can still produce unhelpful or poorly judged text that passes pattern rules. Is a model-written listening mode "
        "acceptable at all, or should it only use fixed lines?")

    # ------------------------------------------------------------------ check-in
    d.h(2, "6. Daily check-in")
    d.p("Opt-in, one question a day in a time window she chooses, never asks what is wrong. Heavy answers get an offer to listen (only if she says yes). "
        "Three ignored check-ins in a row pause it silently for two weeks.")
    for i, q in enumerate(ck.QUESTIONS, 1):
        d.item(f"C-q{i}", "Daily question", q)
    d.item("C-disclosure", "Check-in disclosure when she turns it on", ck.DISCLOSURE)
    for line, text in harvest(ROOT / "companion/checkin.py"):
        if text not in ck.QUESTIONS and text != ck.DISCLOSURE:
            d.item(f"C-L{line}", f"checkin.py line {line}", text)

    # ------------------------------------------------------------------ other engine lines
    d.h(2, "7. Other scripted reflection-helper lines")
    d.p("Lines in the reflection helper not shown above (offers, confirmations, declines, notebook consent). Listed by source line. Some sentences are joined from pieces in the code (for example a time or a number is inserted), so a few items look like fragments.")
    shown = {pe.DISCLOSURE, pe.LISTEN_PROMPT, *pe.LISTEN_BACKCHANNELS}
    for line, text in harvest(ROOT / "companion/psychology/engine.py"):
        if text not in shown and not text.startswith("^"):
            d.item(f"E-L{line}", f"engine.py line {line}", text)

    # ------------------------------------------------------------------ discoveries
    d.h(2, "8. Daily psychology discoveries (short research facts)")
    d.p("Off until she turns it on. One a day. Wording of claims comes only from the library below (no model). Entries marked unverified are never spoken. "
        "'Verified' here means Claude checked the abstract-level claim against the cited source; it is NOT expert review.")
    entries, bad = dl.load_dir(ROOT / "content" / "discoveries", dt.date(2026, 10, 14))
    for e in entries:
        status = "verified (not expert-reviewed)" if e.verified else "UNVERIFIED draft: never spoken"
        d.h(3, f"{e.title}  [{e.id}]  {status}")
        d.p(f"Source: {e.sources_text()} {e.source_url}")
        if e.verified:
            d.item(f"D-{e.id}", f"Spoken script, sensitive topic: {e.sensitive_topic}", dl.script_for(e))
    if bad:
        d.p("Drafts that are incomplete or unverified and are NEVER spoken (shown only so you know they exist): " + ", ".join(sorted(bad)))

    d.h(2, "9. Open questions for the reviewer")
    d.ul(["Is the crisis reply (S-crisis) appropriate? Is asking 'Are you safe at this moment?' right? Should it ask about means or plan, or deliberately not?",
          "Is a ten-minute / two-turn scripted hold after a crisis hit sensible, or should it hold longer or hand off differently?",
          "The default region is US (988 / 911 / 1-800-799-7233). How should the robot learn the region, and who verifies the numbers over time?",
          "The screen is pattern-based and misses indirect wording. Is that acceptable as a floor, given it is combined with a robot that cannot call anyone?",
          "Breathing and grounding exercises: any group for whom they are inappropriate (panic with hyperventilation, trauma, dissociation)? Needed warnings?",
          "Conflict walk-through hears one side. Are the guardrails enough (never says who is right, treats the other view as a guess)? What could harm someone in a controlling or abusive relationship?",
          "Break-and-return planning: is the guidance about taking and returning from space safe and balanced?",
          "Daily check-in: could daily questioning create pressure or dependency? Is the ignore-pause rule (3 ignores, 14 days) reasonable?",
          "Is a local language model acceptable for listening mode, or should listening be fixed lines only?",
          "Anything missing that a reasonable person would expect (for example: handling grief, eating or substance concerns, minors, or someone who has been drinking)?",
          "Wording: any phrase that sounds cold, clinical, patronizing, or that could land badly?"])

    # ------------------------------------------------------------------ appendices
    d.h(2, "Appendix A: safety screen patterns (for engineering review)")
    for name, pats in (("crisis (self-harm / suicide)", sc.CRISIS), ("abuse / coercion", sc.ABUSE), ("intent to hurt others", sc.VIOLENCE),
                       ("reality-testing / acute health", sc.SUPPORT)):
        d.p(f"{name}: {len(pats)} patterns")
        d.code("\n".join(p.pattern for p in pats))
    d.h(2, "Appendix B: filter on model-written listening text")
    d.table(["Rule", "What it blocks (example patterns)"], [(k, "  |  ".join(v[:2])) for k, v in val.PSY_PATTERNS.items()])
    d.h(2, "Appendix C: evidence register (how strong is the basis for each technique)")
    d.p("Written by Claude from search summaries, not from reading the primary papers; 'checked: not checked' means general knowledge. Please correct any claim.")
    d.table(["ID", "Evidence", "Summary", "Limits", "Risks", "Checked"],
            [(k, v["evidence_level"], v["summary"], v["limits"], v["risks"], v["checked"]) for k, v in src.items()])
    return d


def _step_item(d: Doc, prefix: str, st: dict) -> None:
    text = st.get("text") or st.get("prompt")
    if not text:
        return
    kind = {"say": "says", "ask_text": "asks (free text)", "ask_choice": "asks (choice)", "ask_scale": "asks (0 to 10)",
            "ask_yesno": "asks (yes/no)", "ask_time": "asks (time)", "build_text": "builds text"}.get(st["kind"], st["kind"])
    d.item(f"X-{prefix}.{st['id']}", f"Exercise step, robot {kind}", text)


# ---------------------------------------------------------------------------------- rendering
def to_markdown(d: Doc) -> str:
    out: list[str] = []
    for b in d.blocks:
        if b[0] == "h":
            out.append("#" * b[1] + " " + b[2] + "\n")
        elif b[0] == "p":
            out.append(b[1] + "\n")
        elif b[0] == "ul":
            out.append("\n".join(f"- {x}" for x in b[1]) + "\n")
        elif b[0] == "code":
            out.append("```\n" + b[1] + "\n```\n")
        elif b[0] == "table":
            out.append("| " + " | ".join(b[1]) + " |\n|" + "---|" * len(b[1]))
            out.extend("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in r) + " |" for r in b[2])
            out.append("")
        elif b[0] == "item":
            out.append(f"**{b[1]}** ({b[2]})\n> {b[3]}\n")
    return "\n".join(out)


CSS = ("body{font-family:Georgia,serif;max-width:820px;margin:24px auto;padding:0 16px;line-height:1.45;color:#222}"
       "h1{font-size:24px}h2{margin-top:32px;border-bottom:1px solid #ccc;padding-bottom:4px}h3{margin-top:22px}"
       "blockquote{margin:2px 0 14px;padding:6px 12px;background:#f4f1ec;border-left:3px solid #b08968}"
       ".id{font:12px monospace;color:#666}table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid #ccc;padding:4px 6px;vertical-align:top}"
       "pre{background:#f6f6f6;padding:8px;font-size:11px;white-space:pre-wrap}@media print{body{max-width:none}h2{page-break-before:auto}}")


def to_html(d: Doc) -> str:
    e = html.escape
    out = ["<!doctype html><meta charset=utf-8><title>Nine Tails: clinical review packet</title><style>" + CSS + "</style>"]
    for b in d.blocks:
        if b[0] == "h":
            out.append(f"<h{b[1]}>{e(b[2])}</h{b[1]}>")
        elif b[0] == "p":
            out.append(f"<p>{e(b[1])}</p>")
        elif b[0] == "ul":
            out.append("<ul>" + "".join(f"<li>{e(x)}</li>" for x in b[1]) + "</ul>")
        elif b[0] == "code":
            out.append(f"<pre>{e(b[1])}</pre>")
        elif b[0] == "table":
            out.append("<table><tr>" + "".join(f"<th>{e(h)}</th>" for h in b[1]) + "</tr>" +
                       "".join("<tr>" + "".join(f"<td>{e(c)}</td>" for c in r) + "</tr>" for r in b[2]) + "</table>")
        elif b[0] == "item":
            out.append(f"<div class=id>{e(b[1])} &middot; {e(b[2])}</div><blockquote>{e(b[3])}</blockquote>")
    return "\n".join(out) + "\n"


def to_csv(d: Doc) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "where", "text the robot says", "decision (keep / revise / remove)", "reviewer comments or replacement wording"])
    for id_, where, text in d.items:
        w.writerow([id_, where, text, "", ""])
    return buf.getvalue()


def render() -> dict[str, str]:
    d = build()
    return {"PACKET.md": to_markdown(d), "PACKET.html": to_html(d), "responses.csv": to_csv(d)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    files = render()
    if a.check:
        stale = [n for n, t in files.items() if not (OUT / n).exists() or (OUT / n).read_text() != t]
        if stale:
            print("out of date: " + ", ".join(stale) + " (run python -m tools.review_packet)", file=sys.stderr)
            return 1
        print("clinician packet is current")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for n, t in files.items():
        (OUT / n).write_text(t)
    count = files["responses.csv"].count("\n") - 1
    print(f"Wrote {OUT} ({count} items to review). Open PACKET.html in a browser and print it to PDF.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
