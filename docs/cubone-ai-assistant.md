# Cubone AI assistant (owner's own project, NOT part of Nine Tails)

Parked idea. Nothing is built. Kept separate from Nine Tails on purpose: different user, different rules.

## The idea
A personal AI assistant with several model "modes". One mode is a **cybersecurity teacher**: teaches what is needed to become an ethical hacker, and how to secure your own network and privacy. Motivation: the owner's Associate program covers this topic with weak teaching.

## Honest assessment (written before any building)
Pros
- Runs locally (Ollama on the Minisforum) so study notes and lab details stay private.
- A tutor that adapts to the owner's level and quizzes them is a real gap in a weak course.

Cons and risks
- Small local models (3B to 8B) invent facts, commands and tool flags. A teacher that is confidently wrong is worse than no teacher. Needs grounding in real sources (course material, official docs, books) and a "say I don't know" rule.
- Ethical-hacking practice must stay on systems the owner owns or is authorized to test (own lab, CTF platforms). The assistant should refuse to help attack anyone else.
- It does not replace hands-on labs; skills come from doing.

Already exists (check before building, do not rebuild these)
- Hands-on platforms: TryHackMe, Hack The Box Academy, PortSwigger Web Security Academy, OverTheWire, picoCTF.
- Cert paths: CompTIA Security+ then PenTest+ or similar; many free study guides.
- General AI tutors can already be prompted to teach security; the new part would be local, private, curriculum-grounded and tied to the owner's progress.

## Suggested shape when the time comes
1. Mode picker (teacher, general, coding) over one local runtime.
2. Curriculum as a plain file (topics, order, quiz bank) that the owner controls.
3. Retrieval over the owner's own notes and chosen sources only.
4. Progress tracking (what was learned, what to review).
5. Safety rule: lab-only guidance, no help targeting third parties.

## Open questions for the owner
- Which exam or job is the goal (Security+, CEH, OSCP, something else)?
- Which hardware runs it (Minisforum is fine for 7B to 8B models)?
- Does it need voice, or is text enough?
