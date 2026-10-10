import time


class SystemClock:
    def __call__(self) -> float:
        return time.time()

    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
