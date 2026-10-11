# Psychology evidence register

Generated from `companion/psychology/sources/sources.yaml`. Do not edit by hand.
"2026-10-11 secondary summaries" means I read search-result summaries, not the primary papers. "not checked" means written from general knowledge. Evidence level describes this specific use, not the parent therapy.

## Affect labeling (putting feelings into words)
- id: `affect_labeling` | kind: research | **evidence: mixed** | checked: 2026-10-11 secondary summaries
- summary: Naming an emotion can reduce its intensity for some people in some situations (Lieberman and colleagues, 2007 onward).
- limits: A 2018 narrative review frames it as implicit emotion regulation; a 2026 systematic review calls it a conditional strategy that varies by person and context; I found no clear large preregistered replication. Effects are modest and heterogeneous.
- risks: Low. Forced labeling can feel invalidating if the suggested word is wrong, so words are offered as guesses.
- used by: `naming_emotions`
- link: https://journals.sagepub.com/doi/10.1177/1754073917742706
- link: https://www.accesson.kr/healthpsy/v.31/4/1147/59715

## Emotional granularity / differentiation
- id: `emotional_granularity` | kind: research | **evidence: limited** | checked: not checked
- summary: People who distinguish emotions more finely (Barrett; Kashdan) tend to regulate better, mostly in correlational work.
- limits: Mostly correlational; no strong evidence that coaching granularity by itself changes outcomes.
- risks: Low.
- used by: `naming_emotions`

## Separating events from interpretations (thought-record style questions)
- id: `cbt_cognitive_restructuring` | kind: research | **evidence: moderate** | checked: 2026-10-11 secondary summaries
- summary: Cognitive restructuring is a core CBT component. In-session restructuring is associated with better therapy outcomes (4-study meta-analysis, r = .35).
- limits: That finding is correlational and based on few studies; thought records vary so much in format that pooled effects are hard to compute. A short self-guided version delivered by a robot has not been tested.
- risks: Can sound like 'your feelings are wrong' if done badly. The script says feelings are real even when an interpretation turns out to be inaccurate.
- used by: `facts_vs_interpretations`, `conflict_map`, `conflict_navigation`
- link: https://pmc.ncbi.nlm.nih.gov/articles/PMC10440210/
- link: https://jamanetwork.com/journals/jamapsychiatry/article-abstract/2832696

## Reflective listening (Rogers; motivational interviewing OARS)
- id: `reflective_listening` | kind: framework | **evidence: moderate** | checked: not checked
- summary: Accurate reflection of what another person said and felt is a staple of client-centered therapy and motivational interviewing.
- limits: Evidence is for trained use in clinical settings; practicing it with a robot is untested.
- risks: Low. Practice partner is the robot, not the real person.
- used by: `reflective_listening_practice`, `conflict_map`, `conflict_navigation`

## Taking a break when flooded, then returning (Gottman)
- id: `gottman_flooding_break` | kind: research | **evidence: limited** | checked: 2026-10-11 secondary summaries
- summary: Gottman's lab reports that overwhelmed partners (high heart rate) do poorly in discussion and that roughly 20 or more minutes apart, doing something soothing, helps.
- limits: The 20-minute figure comes mostly from the Gottman Institute's own summaries; sources quote 20, 20-40 or 20-30 minutes; lab arguments on cue differ from real fights; I could not check the primary papers. Treat the number as a rule of thumb, not a measured requirement.
- risks: A pause can become avoidance if no return time is agreed, and can feel like abandonment to the other person; the exercise insists on a specific return time and on respecting a request for space. Not appropriate when someone is unsafe.
- used by: `break_and_return_plan`, `conflict_navigation`
- link: https://www.gottman.com/blog/how-stress-affects-relationships/

## Gentle start-up and repair attempts (Gottman)
- id: `gottman_soft_startup` | kind: research | **evidence: limited** | checked: 2026-10-11 secondary summaries
- summary: Observational work links harsh start-ups with worse discussion outcomes and successful repair attempts with better ones.
- limits: Observational, from one research program with a commercial arm; causation is not established. Popular numbers ('5 to 1 ratio') are not used here.
- risks: Can be used to police tone; the engine applies it only to the user's own wording, never to the other person.
- used by: `prepare_difficult_conversation`
- link: https://gottman.com/about/research/couples/

## Observation, feeling, need, request (Nonviolent Communication, Rosenberg)
- id: `nvc` | kind: framework | **evidence: limited** | checked: 2026-10-11 secondary summaries
- summary: A four-part way to phrase a hard message without blame.
- limits: Reviews find mostly small quasi-experimental studies on empathy and conflict skills, no randomized trials, with methodological weaknesses.
- risks: Formulaic phrasing can sound scripted; used as a drafting aid she edits, never as a rule.
- used by: `prepare_difficult_conversation`
- link: https://www.cnvc.org/learn/research/nonviolent-communication-a-literature-review

## Values clarification (ACT)
- id: `values_clarification` | kind: research | **evidence: limited** | checked: 2026-10-11 secondary summaries
- summary: Naming what matters to you supports committed action in ACT; values exercises show positive effects in small and analogue studies.
- limits: A systematic review of 17 studies found positive effects across varied designs; I found no dedicated meta-analysis; many trials are analogue.
- risks: Low. The robot never tells her what her values should be.
- used by: `values_clarification`
- link: https://contextualscience.org/publications/experimental_comparison_two_acceptance_commitment_therapy_act_values_exercises

## Self-compassion break (Neff)
- id: `self_compassion` | kind: research | **evidence: moderate** | checked: 2026-10-11 secondary summaries
- summary: Self-compassion interventions show moderate effects on self-compassion and stress/anxiety/depression in RCT meta-analyses (g about 0.5-0.75).
- limits: Effects are much larger against wait-lists than against active controls (d .86 vs .19 in one meta-analysis); possible publication bias; the evidence is for multi-week programs, not a one-minute prompt.
- risks: Some people find self-kindness uncomfortable at first; she can stop.
- used by: `self_compassion_break`
- link: https://acuresearchbank.acu.edu.au/item/87545/self-compassion-interventions-and-psychosocial-outcomes-a-meta-analysis-of-rcts
- link: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10091769/

## If-then plans (implementation intentions)
- id: `implementation_intentions` | kind: research | **evidence: moderate** | checked: 2026-10-11 secondary summaries
- summary: Gollwitzer and Sheeran's 2006 meta-analysis (94 tests) reports d = 0.65 for goal attainment when people form specific if-then plans.
- limits: Figure taken from citing sources, not the paper; effect sizes differ by domain; a related technique (mental contrasting + IIs) showed g about 0.34 in a later meta-analysis. Works best for simple, concrete behaviors.
- risks: Low. Goals must be her own, not set by someone else.
- used by: `goal_review`
- link: https://stafforini.com/works/gollwitzer-2006-implementation-intentions-and/
- link: https://cancercontrol.cancer.gov/brp/research/constructs/implementation-intentions

## Slow paced breathing (about 6 breaths per minute)
- id: `slow_breathing` | kind: research | **evidence: moderate** | checked: 2026-10-11 secondary summaries
- summary: Breathing near 0.1 Hz maximizes heart-rate variability and baroreflex gain; a 2022 systematic review and meta-analysis examined effects on HRV.
- limits: Benefits for stress and anxiety look plausible but are heterogeneous, with small samples and weak controls in many studies; I did not read the pooled results.
- risks: Dizziness or lightheadedness is possible; people with breathing or heart conditions, or for whom breath focus triggers panic, should skip it. The script offers a stop at any time and never forces a count.
- used by: `slow_breathing`
- link: https://qastaffprofiles.bournemouth.ac.uk/display/journal-article/368158
- link: https://link.springer.com/article/10.1007/s12671-023-02294-2

## 5-4-3-2-1 sensory grounding
- id: `grounding_54321` | kind: practitioner | **evidence: unestablished** | checked: 2026-10-11 secondary summaries
- summary: Widely taught by clinicians as a way to come back to the present.
- limits: I found practitioner handouts and no trials or contraindication guidance; sense order differs between sources.
- risks: Sensory focus can be distressing for some people with trauma or dissociation. It is offered only when she asks or agrees, she can stop at once, and it is not offered during any risk signal.
- used by: `grounding_54321`
- link: https://www.king.edu/wp-content/uploads/2025/06/Grounding-Techniques.pdf

## Behavior, impact, contributing factors, change, repair, follow-through
- id: `accountability_steps` | kind: practitioner | **evidence: unestablished** | checked: not checked
- summary: A six-step reflection that separates explanation from justification and intention from impact.
- limits: Design synthesis written for this project from common repair and accountability practice. No study of this exact sequence.
- risks: Can turn into self-blame. The script never asks her to accept blame where facts are unclear and lets her stop at any step.
- used by: `accountability_steps`, `repair_attempt_reflection`

## Reflecting on a repair attempt
- id: `repair_attempts` | kind: research | **evidence: limited** | checked: 2026-10-11 secondary summaries
- summary: Repair attempts that land (and are followed by action) are linked to better outcomes in observational couple research.
- limits: Observational (Gottman program); small sample sizes in many studies.
- risks: Low.
- used by: `accountability_steps`, `repair_attempt_reflection`
- link: https://gottman.com/about/research/couples/

## Attachment theory as educational background
- id: `attachment_education` | kind: framework | **evidence: mixed** | checked: 2026-10-11 secondary summaries
- summary: Adult attachment is usually described by two dimensions (anxiety and avoidance); whether it is better seen as types or dimensions is still debated.
- limits: Self-report quizzes are not diagnostic. The engine never assigns or reports an attachment style; it may explain the ideas only if she asks.
- risks: Labels can become identities or weapons. No labels are produced or stored.
- used by: safety design only
- link: https://pmc.ncbi.nlm.nih.gov/articles/PMC8266088

## Growth mindset
- id: `growth_mindset` | kind: research | **evidence: mixed** | checked: not checked
- summary: The belief that abilities can change; school-based interventions show small average effects.
- limits: Large-scale evidence is mixed and effects are small; not checked in this session. Used only as gentle framing ('this is a skill you can practice'), not as a promise.
- risks: Can be misused to blame people for not changing.
- used by: `goal_review`

## Known failure modes of AI mental-health chat
- id: `chatbot_risks` | kind: policy | **evidence: limited** | checked: 2026-10-11 secondary summaries
- summary: Commentary and early evaluations list missed suicidal crises, validating harmful beliefs (sycophancy), hallucination and emotional dependency.
- limits: Mostly policy analysis and secondary reports (Georgetown Tech Institute, JAMIA-linked framework, a summary of Stanford work); I did not verify the original studies or the quoted figures.
- risks: This is why crisis handling is scripted and runs before any model, replies are filtered, and the engine encourages real-world support instead of itself.
- used by: safety design only
- link: https://www.law.georgetown.edu/tech-institute/research-insights/insights/reduce-ai-sycophancy-risks/

