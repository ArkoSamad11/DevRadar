import time
from collections import deque


class RateLimiter:
    """Allows a number of calls per client, and in total, within a sliding window."""

    def __init__(
        self, per_client: int, total: int, window: float, clock=time.monotonic
    ) -> None:
        self.per_client = per_client
        self.total = total
        self.window = window
        self.clock = clock
        self.calls: deque[tuple[float, str]] = deque()

    def allow(self, client: str) -> bool:
        now = self.clock()
        while self.calls and self.calls[0][0] <= now - self.window:
            self.calls.popleft()
        if len(self.calls) >= self.total:
            return False
        if sum(1 for _, caller in self.calls if caller == client) >= self.per_client:
            return False
        self.calls.append((now, client))
        return True
