"""Process-local access tokens, session ownership, and request counters."""

from threading import Lock
from time import monotonic


class LocalAuthStore:
    """Support one backend worker when Redis is unavailable in local development."""

    def __init__(self):
        self._values = {}
        self._lock = Lock()

    def _prune(self, now):
        self._values = {key: row for key, row in self._values.items() if now < row[1]}

    def set(self, key, value, *, ex, nx=False):
        with self._lock:
            now = monotonic()
            self._prune(now)
            if nx and key in self._values:
                return False
            self._values[key] = (value, now + ex)
            return True

    def get(self, key):
        with self._lock:
            self._prune(monotonic())
            return self._values.get(key, (None, 0))[0]

    def expire(self, key, seconds):
        with self._lock:
            now = monotonic()
            self._prune(now)
            if key not in self._values:
                return False
            self._values[key] = (self._values[key][0], now + seconds)
            return True

    def delete(self, key):
        with self._lock:
            self._values.pop(key, None)

    def increment_rate(self, key, window):
        with self._lock:
            now = monotonic()
            self._prune(now)
            count, expires = self._values.get(key, (0, now + window))
            self._values[key] = (count + 1, expires)
            return count + 1, int(expires - now)
