import asyncio
import time
from contextlib import asynccontextmanager
from threading import Lock
from typing import Any, AsyncIterator

from .config import get_settings

_semaphore: asyncio.Semaphore | None = None
_state_lock = Lock()
_active_requests = 0
_queued_requests = 0
_started_at = time.time()

_gpu_cache: tuple[float, dict[str, Any] | None] = (0.0, None)
_gpu_lock = asyncio.Lock()


def startup() -> None:
    global _semaphore, _started_at
    _semaphore = asyncio.Semaphore(get_settings().max_concurrent_requests)
    _started_at = time.time()


@asynccontextmanager
async def request_slot() -> AsyncIterator[None]:
    global _active_requests, _queued_requests
    if _semaphore is None:
        startup()
    assert _semaphore is not None

    with _state_lock:
        _queued_requests += 1
    await _semaphore.acquire()
    with _state_lock:
        _queued_requests -= 1
        _active_requests += 1

    try:
        yield
    finally:
        with _state_lock:
            _active_requests -= 1
        _semaphore.release()


def runtime_snapshot() -> dict[str, int]:
    with _state_lock:
        active = _active_requests
        queued = _queued_requests
    return {
        "active_requests": active,
        "queued_requests": queued,
        "max_concurrent_requests": get_settings().max_concurrent_requests,
        "uptime_seconds": max(0, int(time.time() - _started_at)),
    }


async def _read_gpu() -> dict[str, Any] | None:
    try:
        process = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5.0)
        if process.returncode != 0:
            return None
        line = stdout.decode(errors="replace").strip().splitlines()[0]
        name, utilization, memory_used, memory_total, temperature, power = [
            part.strip() for part in line.split(",", 5)
        ]
        return {
            "name": name,
            "utilization_percent": float(utilization),
            "memory_used_mb": float(memory_used),
            "memory_total_mb": float(memory_total),
            "temperature_c": float(temperature),
            "power_w": float(power),
        }
    except (OSError, ValueError, IndexError, asyncio.TimeoutError):
        return None


async def gpu_snapshot() -> dict[str, Any] | None:
    global _gpu_cache
    now = time.monotonic()
    cached_at, cached_value = _gpu_cache
    if now - cached_at < 5:
        return cached_value

    async with _gpu_lock:
        cached_at, cached_value = _gpu_cache
        if now - cached_at < 5:
            return cached_value
        value = await _read_gpu()
        _gpu_cache = (time.monotonic(), value)
        return value
