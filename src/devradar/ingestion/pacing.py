import asyncio
import time


class Pacer:
    """Spaces out request starts by a minimum interval."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._next_start = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        start = max(now, self._next_start)
        self._next_start = start + self.interval
        await asyncio.sleep(start - now)
