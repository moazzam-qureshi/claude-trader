"""Helpers for running async coroutines from synchronous code (Celery tasks).

Celery task functions are synchronous. Each task wraps an `async def` body with
`asyncio.run(...)`. In production workers this is fine, but under
`task_always_eager=True` (integration tests), a task may dispatch another task
via `.apply_async()` which Celery runs inline within the current asyncio.run
loop — yielding 'asyncio.run() cannot be called from a running event loop'.

`run_coro` detects a running loop and offloads the coroutine to a fresh loop
on a worker thread; otherwise it runs it normally.
"""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ts-async-run")


def run_coro[T](coro: Coroutine[object, object, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    future = _executor.submit(asyncio.run, coro)
    return future.result()
