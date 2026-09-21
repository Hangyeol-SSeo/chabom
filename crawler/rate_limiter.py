"""요청 속도 제한 (스펙 3.3): 요청 간 1~3초 딜레이를 기본값으로 둔다."""
from __future__ import annotations

import random
import time


class RateLimiter:
    def __init__(self, min_delay_sec: float = 1.0, max_delay_sec: float = 3.0):
        if min_delay_sec < 0 or max_delay_sec < min_delay_sec:
            raise ValueError("min_delay_sec/max_delay_sec 값이 올바르지 않습니다")
        self.min_delay_sec = min_delay_sec
        self.max_delay_sec = max_delay_sec
        self._last_request_at: float | None = None

    def wait(self) -> None:
        delay = random.uniform(self.min_delay_sec, self.max_delay_sec)
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            remaining = delay - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()
