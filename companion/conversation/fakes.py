from __future__ import annotations

from ..interfaces import LanguageModel


class FakeLanguageModel(LanguageModel):
    """Scripted model for tests. Each script item is a dict {"text","tool_calls"}, a str, an Exception to raise,
    or a callable(system, messages, tools) -> any of those. Records every call."""

    def __init__(self, script=None, default="Okay."):
        self.script, self.default, self.calls = list(script or []), default, []

    def complete(self, system, messages, tools=None):
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        item = self.script.pop(0) if self.script else self.default
        if callable(item):
            item = item(system, messages, tools)
        if isinstance(item, Exception):
            raise item
        return {"text": item, "tool_calls": []} if isinstance(item, str) else item
