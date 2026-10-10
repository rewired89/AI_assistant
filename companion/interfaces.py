"""Replaceable service boundaries. Concrete providers live elsewhere; nothing here
imports a vendor SDK. Which ones need the internet is stated per provider, not here."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class TransportClosed(Exception):
    """No live connection to the device."""


class DeviceTransport(ABC):
    """Moves opaque signed bytes. Knows nothing about message meaning."""

    @abstractmethod
    def send(self, raw: bytes) -> None: ...

    @abstractmethod
    def receive(self) -> list[bytes]:
        """Return all inbound messages available now (may be empty). Never blocks long."""


class LanguageModel(ABC):
    @abstractmethod
    def complete(self, system: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Return {"text": str, "tool_calls": [{"name": str, "arguments": dict}]}.
        Tool calls are requests only; the host validates before anything reaches a device."""


class SpeechRecognizer(ABC):
    @abstractmethod
    def transcribe(self, pcm16_16k_mono: bytes) -> str: ...


class SpeechSynthesizer(ABC):
    @abstractmethod
    def synthesize(self, text: str, style: str = "normal", speed: float = 1.0) -> bytes:
        """Return pcm16 16 kHz mono audio."""


class MemoryStore(ABC):
    @abstractmethod
    def add(self, record: dict) -> str: ...

    @abstractmethod
    def search(self, query: str, kinds: list[str] | None = None, limit: int = 5) -> list[dict]: ...

    @abstractmethod
    def update(self, memory_id: str, changes: dict) -> None: ...

    @abstractmethod
    def delete(self, memory_id: str) -> None: ...
