"""Host side of the device link: a wss:// server the robot dials into.

Why the device dials out: it works behind home NAT/DHCP and the host needs no device IP.
Layers: TLS (confidentiality, server identity via pinned CA) -> WebSocket text frames (one JSON
protocol message each) -> HMAC (message authenticity, in protocol/). This class is a dumb pipe plus
connection hygiene; it never interprets commands.
Hygiene: pre-auth gate (first frame must pass a MAC check within a timeout), one device at a time
(only an *authenticated* newcomer replaces the old connection), 16 KiB frames, text only,
token-bucket rate limit, keepalive pings. No event buffering: messages sent while disconnected raise
TransportClosed and device events produced while offline are lost.
"""
from __future__ import annotations

import asyncio
import queue
import ssl
import threading
import time
from collections import Counter
from typing import Callable

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from protocol import MAX_MESSAGE_BYTES

from ..interfaces import DeviceTransport, TransportClosed


class TokenBucket:
    def __init__(self, rate_per_s: float, burst: int):
        self.rate, self.cap, self.tokens, self.t = rate_per_s, burst, float(burst), time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.cap, self.tokens + (now - self.t) * self.rate)
        self.t = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


def server_ssl_context(cert: str, key: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    return ctx


def client_ssl_context(ca_cert: str) -> ssl.SSLContext:
    """For the device side/tests: trust only our CA, verify hostname, TLS >= 1.2."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(ca_cert)
    return ctx


class WebSocketHostTransport(DeviceTransport):
    def __init__(self, ssl_context: ssl.SSLContext, host: str = "127.0.0.1", port: int = 0,
                 pre_auth_check: Callable[[str], bool] | None = None, pre_auth_timeout_s: float = 10.0,
                 rate_per_s: float = 50, burst: int = 100, ping_interval_s: float = 20, ping_timeout_s: float = 20):
        self._ssl, self._host, self.port = ssl_context, host, port
        self._check = pre_auth_check
        self._pre_auth_timeout = pre_auth_timeout_s
        self._rate, self._burst = rate_per_s, burst
        self._ping = (ping_interval_s, ping_timeout_s)
        self._inbox: queue.SimpleQueue[bytes] = queue.SimpleQueue()
        self._ws = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop: asyncio.Event | None = None
        self.stats: Counter = Counter()
        self.connections = 0  # authenticated connections accepted so far

    # ---- lifecycle ------------------------------------------------------------
    def start(self) -> int:
        ready = threading.Event()
        err: list[BaseException] = []

        def run():
            try:
                asyncio.run(self._main(ready))
            except BaseException as e:  # surfaced to start()
                err.append(e)
                ready.set()

        self._thread = threading.Thread(target=run, daemon=True, name="ws-host")
        self._thread.start()
        if not ready.wait(10) or err:
            raise RuntimeError(f"websocket server failed to start: {err[0] if err else 'timeout'}")
        return self.port

    async def _main(self, ready: threading.Event) -> None:
        self._loop, self._stop = asyncio.get_running_loop(), asyncio.Event()
        async with serve(self._handler, self._host, self.port, ssl=self._ssl, max_size=MAX_MESSAGE_BYTES,
                         ping_interval=self._ping[0], ping_timeout=self._ping[1], max_queue=16) as server:
            self.port = server.sockets[0].getsockname()[1]
            ready.set()
            await self._stop.wait()

    def stop(self) -> None:
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread:
            self._thread.join(5)
        self._ws = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    # ---- DeviceTransport -------------------------------------------------------
    def send(self, raw: bytes) -> None:
        ws, loop = self._ws, self._loop
        if ws is None or loop is None:
            raise TransportClosed("device not connected")
        fut = asyncio.run_coroutine_threadsafe(ws.send(raw.decode()), loop)
        try:
            fut.result(5)
        except (ConnectionClosed, TimeoutError, Exception) as e:
            raise TransportClosed(str(e)) from None

    def receive(self) -> list[bytes]:
        out = []
        while True:
            try:
                out.append(self._inbox.get_nowait())
            except queue.Empty:
                return out

    @staticmethod
    async def _close_hard(ws, code: int, reason: str, grace_s: float = 2.0) -> None:
        """Close a misbehaving peer without waiting politely: its backlog would otherwise sit in front of
        its close reply (read backpressure) and stall us for the full close timeout. Drain-and-discard
        while closing, then abort the socket if the peer still keeps talking."""
        closer = asyncio.ensure_future(ws.close(code, reason))

        async def drain():
            try:
                async for _ in ws:
                    pass
            except ConnectionClosed:
                pass
        try:
            await asyncio.wait_for(asyncio.gather(closer, drain()), grace_s)
        except (asyncio.TimeoutError, TimeoutError):
            ws.transport.abort()

    # ---- per connection ----------------------------------------------------------
    async def _handler(self, ws) -> None:
        try:
            first = await asyncio.wait_for(ws.recv(), self._pre_auth_timeout)
        except (asyncio.TimeoutError, TimeoutError):
            self.stats["preauth_timeout"] += 1
            await ws.close(4001, "auth timeout")
            return
        except ConnectionClosed:
            self.stats["preauth_closed"] += 1
            return
        if not isinstance(first, str) or (self._check is not None and not self._check(first)):
            self.stats["preauth_rejected"] += 1
            await ws.close(4003, "unauthorized")
            return
        old, self._ws = self._ws, ws  # only an authenticated peer can displace the current one
        self.connections += 1
        if old is not None:
            self.stats["replaced"] += 1
            await old.close(4000, "replaced")
        self._inbox.put(first.encode())
        bucket = TokenBucket(self._rate, self._burst)
        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    self.stats["binary_rejected"] += 1
                    await self._close_hard(ws, 1003, "text only")
                    break
                if not bucket.allow():
                    self.stats["rate_limited"] += 1
                    await self._close_hard(ws, 1008, "rate limit")
                    break
                self._inbox.put(msg.encode())
        except ConnectionClosed:
            pass
        finally:
            if self._ws is ws:
                self._ws = None


def mac_gate(secret: bytes, clock: Callable[[], float]) -> Callable[[str], bool]:
    """Pre-auth check for the first frame: well-formed envelope with a valid MAC and fresh timestamp.
    Does not consume the nonce, so DeviceClient can still run the full verify() on the same frame."""
    from protocol import Authenticator, ProtocolError, decode
    auth = Authenticator(secret, clock)

    def check(raw: str) -> bool:
        try:
            auth.verify_mac(decode(raw))
            return True
        except ProtocolError:
            return False
    return check
