
import logging
import os
import time

logger = logging.getLogger("evgrid.tomtom_keys")

EXHAUST_COOLDOWN_S = float(os.getenv("TOMTOM_EXHAUST_COOLDOWN_S", "900"))


class TomTomKeyPool:
    def __init__(self, keys):
        self._keys = [k.strip() for k in keys if k and k.strip()]
        if not self._keys:
            raise RuntimeError(
                "No TomTom API keys configured. Set TOMTOM_API_KEYS "
                "(comma-separated) or TOMTOM_API_KEY in .env"
            )
        self._index = 0
        self._exhausted = {}  
        self._invalid = set()  

    @classmethod
    def from_env(cls):
        multi = os.getenv("TOMTOM_API_KEYS")
        if multi:
            return cls(multi.split(","))
        single = os.getenv("TOMTOM_API_KEY")
        return cls([single] if single else [])

    def _expire(self):
        now = time.monotonic()
        for key in [k for k, t in self._exhausted.items() if now - t >= EXHAUST_COOLDOWN_S]:
            del self._exhausted[key]
            logger.info(f"TomTom key ending ...{key[-4:]} is being retried after cooldown")

    @property
    def keys(self):
        return list(self._keys)

    @property
    def current(self):
        return self._keys[self._index]

    def mark_exhausted(self, key):
        self._expire()
        if key not in self._exhausted:
            self._exhausted[key] = time.monotonic()
            logger.warning(
                f"TomTom key ending ...{key[-4:]} looks exhausted "
                f"({len(self._exhausted)}/{len(self._keys)} keys exhausted)"
            )

    def mark_invalid(self, key):
        if key not in self._invalid:
            self._invalid.add(key)
            logger.error(
                f"TomTom key ending ...{key[-4:]} is invalid (HTTP 401) and will be skipped. "
                "Remove it from TOMTOM_API_KEYS."
            )

    def _usable(self, key):
        return key not in self._exhausted and key not in self._invalid

    def rotate(self):
        self._expire()
        for _ in range(len(self._keys)):
            self._index = (self._index + 1) % len(self._keys)
            if self._usable(self.current):
                return True
        return False

    def all_exhausted(self):
        self._expire()
        return not any(self._usable(k) for k in self._keys)


def request_with_rotation(session, url, params, key_pool, timeout=30):

    response = None
    attempts = 0

    while attempts < len(key_pool.keys):
        attempts += 1
        current = key_pool.current
        response = session.get(url, params={**params, "key": current}, timeout=timeout)

        if response.status_code not in (401, 403, 429):
            return response

        if response.status_code == 401:
            key_pool.mark_invalid(current)
        else:
            key_pool.mark_exhausted(current)
        if not key_pool.rotate():
            break

    return response