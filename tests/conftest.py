"""Shared pytest fixtures. Real fixtures (testcontainers) added per-task."""
import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
