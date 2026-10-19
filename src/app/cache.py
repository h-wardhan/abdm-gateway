"""
Small key/value cache with TTL, used for the ABDM gateway token, ABDM public key, txn ownership
and the duplicate-request guard.

Uses Redis when REDIS_URL is set, otherwise an in-process dict (fine for local dev / a single worker).
"""
import json
import threading
import time


class MemoryCache:
    backend = "memory"

    def __init__(self):
        self._data = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            value, expires_at = item
            if expires_at is not None and expires_at <= time.monotonic():
                del self._data[key]
                return None
            return value

    def set(self, key, value, ttl=None):
        with self._lock:
            self._data[key] = (value, time.monotonic() + ttl if ttl else None)

    def add(self, key, value, ttl=None):
        """Set only if the key is absent (or expired). Returns True if it was set."""
        with self._lock:
            item = self._data.get(key)
            if item is not None and (item[1] is None or item[1] > time.monotonic()):
                return False
            self._data[key] = (value, time.monotonic() + ttl if ttl else None)
            return True

    def delete(self, key):
        with self._lock:
            self._data.pop(key, None)

    def ping(self):
        return True


class RedisCache:
    backend = "redis"

    def __init__(self, url, prefix="abdmgw:"):
        import redis

        self._client = redis.Redis.from_url(url, socket_timeout=3, socket_connect_timeout=3)
        self._prefix = prefix

    def get(self, key):
        raw = self._client.get(self._prefix + key)
        return json.loads(raw) if raw is not None else None

    def set(self, key, value, ttl=None):
        self._client.set(self._prefix + key, json.dumps(value), ex=int(ttl) if ttl else None)

    def add(self, key, value, ttl=None):
        return bool(self._client.set(self._prefix + key, json.dumps(value), ex=int(ttl) if ttl else None, nx=True))

    def delete(self, key):
        self._client.delete(self._prefix + key)

    def ping(self):
        return bool(self._client.ping())


class Cache:
    """Proxy so modules can import `cache` before the app is created."""

    def __init__(self):
        self._impl = MemoryCache()

    def init_app(self, app):
        url = app.config.get("REDIS_URL")
        self._impl = RedisCache(url) if url else MemoryCache()
        app.extensions["cache"] = self

    @property
    def backend(self):
        return self._impl.backend

    def get(self, key):
        return self._impl.get(key)

    def set(self, key, value, ttl=None):
        self._impl.set(key, value, ttl)

    def add(self, key, value, ttl=None):
        return self._impl.add(key, value, ttl)

    def delete(self, key):
        self._impl.delete(key)

    def ping(self):
        return self._impl.ping()
