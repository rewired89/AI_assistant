# Psychological Growth Engine (design only, not implemented)

Position: education and reflection support, not therapy, diagnosis or relationship arbitration. Opt-in, pausable, private to her, separate module from personality/tamagotchi.

## Hard rules (to be enforced by tests in Phase 9)
No diagnosis or attachment-style labels as fact. No scores. No siding automatically with whoever is describing a conflict, and no judging her therapy. No shame, guilt, fear or dependency hooks. Sensitive reflections saved only with consent. Danger/abuse/coercion signals route to human help, not compromise advice. Support both taking a break and returning at an agreed time.

## Source plan (to verify before use; none has been reviewed yet)
Candidate frameworks, named so they can be checked, not yet cited as evidence: CBT thought records (Beck), DBT skills incl. distress tolerance (Linehan), self-compassion (Neff), nonviolent communication (Rosenberg), Gottman research on conflict and repair, emotional granularity (Barrett), values clarification/ACT. For each exercise, record evidence level, limits and risks in `companion/psychology/sources/` before implementing. Popular-press claims (e.g. Gottman "ratios", attachment typing quizzes) need extra scrutiny.

## Open design risks
- The owner is the one building this and has a stake in a specific dispute. Mitigation: his input never enters her psychology store; scenarios are tested with both-sided synthetic cases; no "report" feature, ever.
- A cute pet that nudges behavior can feel like covert influence. Mitigation: transparent opt-in text, visible mode indicator, pause switch.
- LLMs sycophantically validate. Needs adversarial synthetic tests, not just prompts.
