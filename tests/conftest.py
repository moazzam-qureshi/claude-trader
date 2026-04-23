"""Shared pytest fixtures."""
from __future__ import annotations

import os
import sys

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _parse_async_url(url: str) -> tuple[str, str, str, str, str]:
    parsed = url.replace("postgresql+asyncpg://", "")
    userpass, hostdb = parsed.split("@", 1)
    user, password = userpass.split(":", 1)
    hostport, db = hostdb.split("/", 1)
    host, port = hostport.split(":", 1)
    return user, password, db, host, port


def _reset_module_singletons() -> None:
    """Re-import-time caches inside the package keep stale engines/settings alive
    across tests. Clear them so each test observes the env vars we just set.
    """
    import trading_sandwich.config as cfg
    cfg._settings = None

    try:
        import trading_sandwich.db.engine as eng
        eng._engine = None
        eng._session_factory = None
    except ImportError:
        pass

    # Celery's app is module-level; its broker URL is baked in at import time
    # and its connection pool is cached on first use. If the app was already
    # imported by a previous test, repoint it at the current env and drop the
    # pool so the next send_task uses the new broker.
    if "trading_sandwich.celery_app" in sys.modules:
        from trading_sandwich.celery_app import app as celery_app
        # conf.broker_url and result_backend are live env-backed views and
        # already reflect the monkeypatched env. But Celery caches:
        #   - app._pool          (broker connection pool)
        #   - app.amqp           (@cached_property holding producer_pool)
        #   - app.backend        (@cached_property holding result-backend client)
        # Each caches a connection bound to the URL seen at first use; all
        # three must be cleared so the next send_task / .get() reconnects to
        # the URL currently in config.
        celery_app._pool = None
        # Celery stashes the result backend in _backend_cache (thread-safe
        # backends) or in _local.backend (non-thread-safe). Clear both.
        celery_app._backend_cache = None
        if hasattr(celery_app._local, "backend"):
            del celery_app._local.backend
        celery_app.__dict__.pop("amqp", None)
        # Reset eager mode so the E2E test's opt-in doesn't leak into other
        # integration tests that rely on dispatches reaching a real broker.
        celery_app.conf.task_always_eager = False
        celery_app.conf.task_eager_propagates = False


@pytest.fixture
def env_for_postgres(monkeypatch) -> callable:
    """Return a function that, given an asyncpg URL, wires the process env to it
    and clears the package's cached singletons. Monkeypatch restores env after
    the test so no leakage to later tests.
    """
    def _apply(async_url: str) -> None:
        user, password, db, host, port = _parse_async_url(async_url)
        monkeypatch.setenv("POSTGRES_USER", user)
        monkeypatch.setenv("POSTGRES_PASSWORD", password)
        monkeypatch.setenv("POSTGRES_DB", db)
        monkeypatch.setenv("POSTGRES_HOST", host)
        monkeypatch.setenv("POSTGRES_PORT", port)
        monkeypatch.setenv("CELERY_BROKER_URL", os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0"))
        monkeypatch.setenv("CELERY_RESULT_BACKEND", os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"))
        _reset_module_singletons()
    return _apply


@pytest.fixture
def env_for_redis(monkeypatch) -> callable:
    """Return a function that points Celery at the given redis:// URL (db 0 for
    broker, db 1 for results) via monkeypatch and clears cached singletons.
    """
    def _apply(redis_url: str) -> None:
        broker = redis_url
        backend = redis_url.rsplit("/", 1)[0] + "/1"
        monkeypatch.setenv("CELERY_BROKER_URL", broker)
        monkeypatch.setenv("CELERY_RESULT_BACKEND", backend)
        _reset_module_singletons()
    return _apply
