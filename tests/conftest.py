import pytest
from companion.application.device_client import DeviceClient, LoopbackTransport
from protocol import Authenticator
from simulator import SimulatedDevice, Storage

SECRET = b"test-secret-0123456789abcdef"


class FakeClock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def advance(self, s):
        self.t += s


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def make_device(tmp_path, clock):
    def _make(**kw):
        return SimulatedDevice(SECRET, Storage(tmp_path / "state.json"), clock, **kw)
    return _make


@pytest.fixture
def device(make_device):
    d = make_device()
    d.boot()
    return d


@pytest.fixture
def client(device, clock):
    return DeviceClient(LoopbackTransport(device), Authenticator(SECRET, clock), clock)
