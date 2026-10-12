"""Spec 39 'AI provider replacement': nothing but the LanguageModel interface knows which provider is in use."""
import pytest

from companion.conversation import FakeLanguageModel, LLMUnavailable
from companion.conversation.engine import SAFE_FALLBACK
from companion.interfaces import LanguageModel
from tests.companion.test_conversation import call, make


class OtherProvider(LanguageModel):
    """A second, unrelated implementation: different class, different internals, same two-key contract."""
    is_local = True

    def __init__(self, reply, tools=()):
        self.reply, self.tools, self.n = reply, list(tools), 0

    def complete(self, system, messages, tools=None):
        self.n += 1
        return {"text": self.reply, "tool_calls": list(self.tools)}


def test_the_model_can_be_swapped_while_running_and_the_rules_stay_the_same(clock):
    eng, _ = make(clock, FakeLanguageModel(["Hello from provider one."]))
    assert "provider one" in eng.respond("hi").text
    two = OtherProvider("Hello from provider two.")
    eng.llm = two                                           # nothing else is rebuilt
    assert "provider two" in eng.respond("hi again").text and two.n == 1


def test_every_provider_goes_through_the_same_output_filter(clock):
    for model in (FakeLanguageModel(["Please don't leave me alone."] * 3), OtherProvider("Please don't leave me alone.")):
        eng, _ = make(clock, model)
        assert eng.respond("bye").text == SAFE_FALLBACK        # the filter belongs to the host, not to a provider


def test_every_provider_gets_the_same_tool_validation(clock):
    bad = [call("set_gpio", pin=4, level=1), call("show_emotion", name="happy")]
    for model in (FakeLanguageModel([{"text": "ok", "tool_calls": bad}]), OtherProvider("ok", bad)):
        eng, _ = make(clock, model)
        r = eng.respond("hello")
        assert r.animation == "happy" and eng.stats["rejected_tool_calls"] == 1     # the raw device command is refused


def test_a_provider_that_fails_is_a_scripted_fallback_not_a_crash(clock):
    class Down(LanguageModel):
        def complete(self, system, messages, tools=None):
            raise LLMUnavailable("down")
    eng, _ = make(clock, Down())
    with pytest.raises(LLMUnavailable):                     # the contract; the voice pipeline speaks a scripted line
        eng.respond("hello")
