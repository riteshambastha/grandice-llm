import time
from collections import defaultdict, deque
from collections.abc import Hashable
from threading import Lock

WINDOW_SECONDS = 60.0

_hits: dict[Hashable, deque[float]] = defaultdict(deque)
_lock = Lock()


def check(key_id: Hashable, limit: int) -> tuple[bool, int]:
    """Sliding-window limiter. Returns (allowed, seconds_until_retry).

    State is in-process, which is correct for the single-worker deployment this
    gateway runs as. Moving to multiple workers would need shared storage.
    """
    now = time.monotonic()
    cutoff = now - WINDOW_SECONDS

    with _lock:
        bucket = _hits[key_id]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= limit:
            return False, max(1, int(bucket[0] + WINDOW_SECONDS - now) + 1)

        bucket.append(now)
        return True, 0


def check_many(
    limits: list[tuple[Hashable, int, str]],
) -> tuple[bool, str | None, int | None, int]:
    """Atomically enforce multiple hierarchical limits.

    Returns ``(allowed, scope, limit, retry_after)``. A denied request is not
    counted against any other scope.
    """
    now = time.monotonic()
    cutoff = now - WINDOW_SECONDS

    with _lock:
        for bucket_id, _, _ in limits:
            bucket = _hits[bucket_id]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()

        for bucket_id, limit, scope in limits:
            bucket = _hits[bucket_id]
            if len(bucket) >= limit:
                retry_after = max(
                    1, int(bucket[0] + WINDOW_SECONDS - now) + 1
                )
                return False, scope, limit, retry_after

        for bucket_id, _, _ in limits:
            _hits[bucket_id].append(now)

    return True, None, None, 0
