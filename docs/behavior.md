# Behavior engine

`BehaviorEngine.react(stimulus, **ctx) -> Plan`. Stimuli: wake, touch, listening_start, thinking, speaking_start/end, idle_tick, host_disconnected/connected, battery_warning/ok/low/critical, sleep_entering, interaction.
Plan = animation request + voice-style hint + `Intent`s (topic and tone, not text). Text generation must stay inside each intent's `grounding` (facts it may assert) and `constraints` (always: no mentioning absence, no distress, no begging for attention).

Rules: mood picks the idle animation; personality traits bias variety (playful, mischievous) and warmth (affectionate x relationship); proactive behavior needs a grounded reason (strong curiosity or playfulness, or caller-supplied memory refs), is capped per day with minimum gaps, and is blocked when sleeping, quiet mode, quiet hours, low energy, offline, low battery, or within 10 min of the last interaction. Battery low/critical produce no chatter. Growth milestones trigger a celebration only when growth is enabled.
Limits: proactive counters and the battery-offer flag are in memory (reset on restart); quiet hours use the state's timezone; animation speed hint exists on the state but is not sent (protocol has no speed field); no dialogue text yet.
