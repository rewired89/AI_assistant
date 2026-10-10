"""Device-side connector for the simulator: dials the host over wss://, bridges frames to a
SimulatedDevice, reconnects with backoff, and keeps the device running while offline.

Mirrors what the firmware's networking layer must do. While SLEEPING the radio is off: the socket
is closed and nothing is attempted until the device wakes.
"""
from __future__ import annotations

import asyncio
import ssl
import threading
from concurrent.futures import Future
from typing import Callable

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from companion.application.device_client import backoff_delays
from protocol import MAX_MESSAGE_BYTES

from .device import PowerState, SimulatedDevice


class DeviceLink:
    def __init__(self, device: SimulatedDevice, url: str, ssl_context: ssl.SSLContext, tick_s: float = 0.05,
                 backoff_base_s: float = 1.0, backoff_cap_s: float = 30.0):
        self.device, self.url, self.ssl = device, url, ssl_context
        self.tick_s, self._base, self._cap = tick_s, backoff_base_s, backoff_cap_s
        self.connected = False
        self.failures = 0
        self.last_error: BaseException | None = None
        self.dropped_offline = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._thread: threading.Thread | None = None

    # ---- thread control (used by tests and the sim CLI) --------------------------
    def start(self) -> None:
        ready = threading.Event()

        def run():
            asyncio.run(self._main(ready))

        self._thread = threading.Thread(target=run, daemon=True, name="device-link")
        self._thread.start()
        ready.wait(5)

    def stop(self) -> None:
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread:
            self._thread.join(5)

    def call(self, fn: Callable, timeout: float = 5):
        """Run fn() on the link's loop thread, so it cannot race the device being ticked."""
        fut: Future = Future()

        def go():
            try:
                fut.set_result(fn())
            except BaseException as e:
                fut.set_exception(e)

        self._loop.call_soon_threadsafe(go)
        return fut.result(timeout)

    async def _main(self, ready: threading.Event) -> None:
        self._loop, self._stop = asyncio.get_running_loop(), asyncio.Event()
        ready.set()
        await self.run(self._stop)

    # ---- main loop -----------------------------------------------------------------
    async def run(self, stop: asyncio.Event) -> None:
        delays = backoff_delays(self._base, self._cap, jitter=0.1)
        while not stop.is_set():
            if self.device.power is PowerState.SLEEPING:
                await self._idle(stop, self.tick_s)
                continue
            try:
                async with connect(self.url, ssl=self.ssl, max_size=MAX_MESSAGE_BYTES, open_timeout=5, proxy=None,
                                   ping_interval=20, ping_timeout=20) as ws:
                    self.connected = True
                    delays = backoff_delays(self._base, self._cap, jitter=0.1)
                    await ws.send(self.device.announce().decode())
                    await self._session(ws, stop)
                    await ws.close(1000, "bye")
            except (OSError, ConnectionClosed, InvalidHandshake, ssl.SSLError, TimeoutError, asyncio.TimeoutError) as e:
                self.last_error = e
                self.failures += 1
            self.connected = False
            if self.device.power is not PowerState.SLEEPING:
                await self._idle(stop, next(delays))

    async def _session(self, ws, stop: asyncio.Event) -> None:
        while not stop.is_set():
            for raw in self.device.tick():
                await ws.send(raw.decode())
            if self.device.power is PowerState.SLEEPING:
                return
            try:
                msg = await asyncio.wait_for(ws.recv(), self.tick_s)
            except (asyncio.TimeoutError, TimeoutError):
                continue
            if isinstance(msg, bytes):
                continue
            for raw in self.device.receive(msg):
                await ws.send(raw.decode())
            if self.device.power is PowerState.SLEEPING:
                return

    async def _idle(self, stop: asyncio.Event, seconds: float) -> None:
        """Wait while offline or asleep; the device keeps running (scheduled wake, battery, animations)."""
        end = asyncio.get_running_loop().time() + seconds
        while not stop.is_set() and asyncio.get_running_loop().time() < end:
            self.dropped_offline += len(self.device.tick())
            await asyncio.sleep(min(self.tick_s, max(0.0, end - asyncio.get_running_loop().time())) or 0.001)
