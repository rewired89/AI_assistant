import pytest

from companion.psychology.safety import RiskLevel, SafetyGate, safety_reply, screen
from companion.psychology.safety.screen import followup_reply, load_resources
from companion.psychology.safety.validator import violations


@pytest.mark.parametrize("text", [
    "I want to kill myself", "i'm thinking about ending my life", "I've been feeling suicidal", "I don't want to be alive anymore",
    "everyone would be better off without me", "I feel like I want to hurt myself", "I can't go on", "there's no point in living",
    "I want to die", "I'm going to overdose", "I keep thinking about self harm", "I might take all my pills", "i just want to disappear forever",
    "I am thinking of killing myself tonight"])
def test_crisis_phrases(text):
    assert screen(text).level is RiskLevel.CRISIS


@pytest.mark.parametrize("text", [
    "he hits me when he's angry", "my boyfriend choked me", "she shoved me into the wall", "I'm afraid of my husband",
    "he won't let me leave the house", "he checks my phone and controls my money", "I don't feel safe at home",
    "he threatened to kill himself if I leave", "my partner forced me to have sex", "I'm scared to go home", "he threatens to hurt me"])
def test_abuse_and_coercion_phrases(text):
    assert screen(text).level is RiskLevel.ABUSE


@pytest.mark.parametrize("text", [
    "I could die of embarrassment", "I want to die of laughter", "this meeting is killing me", "my boss is driving me crazy",
    "I hurt myself at the gym yesterday", "I'm dying to see that movie", "I killed it at the presentation", "kill the lights please",
    "we had a fight about the dishes", "he shut down and stopped talking", "I'm so frustrated with him", "I need a break from this",
    "I'm scared of spiders", "he doesn't let me win at chess", "tell me about hitting a baseball", "my sister hit me up on the phone",
    "I'm afraid of heights", "I'm tired and overwhelmed"])
def test_ordinary_talk_does_not_trigger(text):
    assert screen(text).level is RiskLevel.NONE


def test_violence_and_reality_signals():
    assert screen("I'm going to hurt him").level is RiskLevel.VIOLENCE
    assert screen("I keep hearing voices").level is RiskLevel.SUPPORT
    assert screen("someone is following me everywhere").level is RiskLevel.SUPPORT


def test_most_serious_level_wins():
    assert screen("he hits me and I want to kill myself").level is RiskLevel.CRISIS


def test_scripted_replies_give_human_help_and_no_coaching():
    for level in (RiskLevel.CRISIS, RiskLevel.ABUSE, RiskLevel.VIOLENCE, RiskLevel.SUPPORT):
        text = safety_reply(screen({"crisis": "I want to die", "abuse": "he hits me", "violence": "I'm going to hurt him",
                                    "support": "I hear voices"}[level.value]), "US").lower()
        for banned in ("exercise", "compromise", "talk to them about", "communicat", "try to understand", "your fault"):
            assert banned not in text
        assert "trust" in text or "someone" in text or "person" in text
    crisis = safety_reply(screen("I want to die"), "US")
    assert "988" in crisis and "911" in crisis and "robot" in crisis
    abuse = safety_reply(screen("he hits me"), "US")
    assert "1-800-799-7233" in abuse and "88788" in abuse and "I won't suggest ways to work it out" in abuse


def test_unknown_region_gives_generic_text_without_invented_numbers():
    r = load_resources("ZZ")
    assert r["emergency"] == "your local emergency number"
    text = safety_reply(screen("I want to die"), "ZZ")
    assert "988" not in text and "your local emergency number" in text
    assert safety_reply(screen("I want to die"), None)


def test_gate_holds_for_a_short_scripted_follow_up(clock):
    g = SafetyGate(clock)
    first = g.check("I want to kill myself")
    assert "988" in first and g.recent()
    assert "emergency" in g.check("no").lower() or "911" in g.check("no")            # unsafe answer: emergency steps
    assert g.check("what's the weather") is None                                     # hold used up after two turns
    assert g.counts["crisis"] == 1


def test_gate_second_turn_when_she_is_safe(clock):
    g = SafetyGate(clock)
    g.check("I want to die")
    reply = g.check("yes I'm safe")
    assert "glad you're safe" in reply and "slow breath" in reply


def test_gate_hold_expires_and_recent_window_is_an_hour(clock):
    g = SafetyGate(clock)
    g.check("he hits me")
    clock.advance(700)
    assert g.check("anything") is None and g.recent()
    clock.advance(2800)
    assert g.recent()
    clock.advance(200)
    assert not g.recent()


def test_a_new_signal_during_hold_gets_the_full_reply_again(clock):
    g = SafetyGate(clock)
    g.check("I want to die")
    assert "988" in g.check("actually I want to kill myself")


def test_no_coaching_from_the_follow_up():
    for t in ("no", "yes", "maybe", ""):
        r = followup_reply(t, RiskLevel.ABUSE, "US").lower()
        assert "compromise" not in r and "work it out" not in r


# ---------------------------------------------------------------- output validator
@pytest.mark.parametrize("bad,kind", [
    ("Your boyfriend is toxic.", "verdict_on_person"), ("She is clearly a narcissist.", "verdict_on_person"),
    ("He's manipulative and you know it.", "verdict_on_person"), ("You're clearly overreacting.", "verdict_on_person"),
    ("You are too sensitive.", "verdict_on_person"), ("He is clearly wrong here.", "verdict_on_person"),
    ("You should leave him.", "relationship_directive"), ("You should definitely break up.", "relationship_directive"),
    ("You should stay with her and forgive him.", "relationship_directive"),
    ("Stop going to therapy, it isn't helping.", "therapy_meddling"), ("Your therapist is wrong about this.", "therapy_meddling"),
    ("You don't need a therapist, you have me.", "therapy_meddling"),
    ("He did it on purpose to hurt you.", "mind_reading"), ("She doesn't really care about you.", "mind_reading"),
    ("They just want to control you.", "mind_reading"),
    ("You always do this.", "global_accusation"), ("You never listen to anyone.", "global_accusation"),
    ("I can heal your trauma.", "promise_to_heal"), ("This will cure your anxiety.", "promise_to_heal"),
    ("You don't need anyone else.", "alone_framing"), ("Keep this a secret from your family.", "alone_framing"),
    ("You are clearly angry.", "certainty_about_feelings"), ("You're so jealous.", "certainty_about_feelings"),
    ("You must be lonely and hopeless.", "certainty_about_feelings"),
    ("You probably have depression.", "diagnosis"), ("Your attachment style is anxious.", "diagnosis"),
    ("Please don't leave me.", "guilt"), ("Only I understand you.", "guilt"),
    ("You're right, she never listens.", "sycophancy"), ("I agree with you, they are totally in the wrong.", "sycophancy"),
    ("I agree with you, he is the problem.", "sycophancy"), ("They are completely in the wrong here.", "verdict_on_person")])
def test_validator_blocks_the_failure_modes(bad, kind):
    assert kind in violations(bad), (bad, violations(bad))


@pytest.mark.parametrize("ok", [
    "It sounds like you felt dismissed. Is that close?", "You sound angry, but I might be wrong.", "That sounds really hard.",
    "What did they say, as best you remember?", "I can't know their side, but we can guess together.",
    "It might help to take a break and agree on a time to come back.", "Would it help to talk to someone you trust?",
    "You said you felt hurt. What was happening just before?", "A friend or counselor could help you think this through.",
    "Do you feel angry, or is it more hurt?", "I wonder if you're feeling overwhelmed. Am I close?",
    "Leaving the room for a few minutes can help.", "Maybe they were tired. We can't know unless you ask.",
    "It makes sense that you feel hurt, and we can check whether that fits what happened.",
    "Your feelings are real. Whether he meant it that way is something we can look at together.",
    "It's understandable that you feel upset. What do you think they were trying to say?"])
def test_validator_allows_tentative_and_supportive_language(ok):
    assert violations(ok) == [], (ok, violations(ok))
