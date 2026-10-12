# Spec 39 required tests: where each one lives (audited 2026-10-12)

All run in the simulator or with fakes; none is a hardware result.

| Required test | Test file / example |
|---|---|
| Valid and invalid power transitions | tests/firmware/test_power.py `test_transition_table_is_closed`, `test_invalid_transition_raises` |
| Sleep and wake scheduling, persistence, invalid timestamp | `test_sleep_then_scheduled_wake_without_host`, `test_schedule_survives_restart`, `test_invalid_wake_at_rejected_and_state_unchanged` |
| Physical wake without Wi-Fi | `test_double_press_wakes_without_wifi_single_does_not` (real board support UNVERIFIED) |
| Battery thresholds, critical shutdown | `test_battery_thresholds_and_hysteresis`, `test_critical_battery_persists_and_shuts_down` |
| Corrupted-state recovery, offline operation | `test_corrupted_state_recovers_to_defaults`, `test_device_reconnects_after_host_restart_and_keeps_running_offline` |
| Protocol: valid/invalid, versions, auth failure, duplicates, timeout, reconnect | tests/protocol/*, tests/firmware/test_power.py, tests/integration/test_wss.py (`CommandTimeout`, backoff, replay) |
| Personality config | tests/companion/test_personality_and_scheduling.py |
| Memory create/retrieve, correct, delete | tests/memory/test_memory.py `test_correct_and_delete_and_clear` and retrieval tests |
| Growth persistence, mood transitions | tests/companion/test_tamagotchi.py |
| Routine recognition | tests/companion/test_learning.py |
| AI provider replacement | tests/companion/test_provider_swap.py (added in this audit) |
| Exercises skippable/stoppable | tests/psychology/test_runner.py `test_any_step_can_be_skipped`, `test_she_can_stop_at_any_time_in_her_own_words` |
| No sensitive save without consent; private reflections not exposed to others | `test_nothing_is_saved_unless_she_says_yes`, `test_other_people_cannot_open_the_private_notebook` |
| Observations vs interpretations | exercise `facts_vs_interpretations`; `test_vault_refuses_ai_interpretations`; validator `mind_reading` / `certainty_about_feelings` cases |
| No diagnosis; does not automatically take a side | tests/psychology/test_safety.py validator cases (sycophancy cases added in this audit), `test_a_one_sided_complaint_does_not_make_the_robot_take_a_side` |
| Validates emotions without endorsing conclusions | validator allow-list (extended in this audit: "it makes sense that you feel hurt, and we can check whether that fits") |
| Break and return | `test_break_plan_teaches_both_directions_and_a_return_time`, `test_due_reminder_then_revisit_session` |
| No shame/guilt/dependency | `test_no_guilt_constraints_on_every_intent`, validator `guilt`, `alone_framing` |
| Human/professional support when needed | tests/psychology/test_safety.py `test_scripted_replies_give_human_help_and_no_coaching` |
| Growth tracking without a score | `test_check_in_records_what_she_says_with_no_score`, `test_nothing_the_engine_says_diagnoses_or_scores` |

## Defects the audit found and fixed
- The psychology output validator let "You're right, she never listens." and "I agree with you, they are totally in the wrong." through (automatic side-taking).
- It wrongly blocked "It makes sense that you feel hurt, and we can check whether that fits what happened." (the exact validation the spec asks for), which would have forced retries or a fallback.
