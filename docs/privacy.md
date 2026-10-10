# Privacy

Principles: audio only on explicit push-to-talk first; device shows when capturing (LED/screen, hardware TBD); no continuous recording by default; local-first processing where practical, and each provider documents whether it uses the internet.
Stores kept separate: device telemetry, companion memory, sensitive reflections (psychology), relationship reflections. Reflections are never visible to the owner/partner without her explicit per-item permission. View, correct, delete, export, and retention controls are required before any memory feature ships.
Status: ordinary memory and the reflection vault are implemented (`companion/memory`, tested): separate SQLite files bound to one user_id, AI inferences held `pending` until confirmed, vault off by default with consent per write and no AI interpretations, hard delete, per-category clear, retention purge, export.
Gaps: no encryption at rest (needs SQLCipher or OS-level disk encryption), no sentiment/semantic retrieval (lexical only), no UI for review yet, sensitive-term screen is a crude keyword list that will miss things.

Conversation (LLM): the default adapter only talks to loopback/private addresses and ignores proxy env vars; sending conversations off-network needs `allow_remote=True` in code. The prompt receives her name and up to 5 retrieved active memories (never pending ones, never reflections). Chat history stays in RAM (last 6 exchanges) and is not persisted. Anything the model proposes to remember is checked against her own words; ungrounded proposals wait for her yes.
