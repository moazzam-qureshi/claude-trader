"""Shared async engine + session factory.

Engines use NullPool so each task invocation opens fresh connections. Celery
tasks are short-lived and — in integration tests with task_always_eager — may
run on ad-hoc event loops; sharing pooled connections across loops causes
asyncpg's "Future attached to a different loop" error. NullPool is the right
fit for this workload; pgbouncer (Phase 1) provides the actual connection
pooling in front of Postgres.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from trading_sandwich.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory
