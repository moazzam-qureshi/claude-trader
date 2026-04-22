"""Celery application instance, shared by all workers and beat."""
from __future__ import annotations

from celery import Celery

from trading_sandwich.config import get_settings
from trading_sandwich.logging import configure_logging

configure_logging()
settings = get_settings()

app = Celery(
    "trading_sandwich",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "trading_sandwich.features.worker",
        "trading_sandwich.signals.worker",
        "trading_sandwich.outcomes.worker",
    ],
)

app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_default_queue="features",
    task_routes={
        "trading_sandwich.features.worker.*": {"queue": "features"},
        "trading_sandwich.signals.worker.*": {"queue": "signals"},
        "trading_sandwich.outcomes.worker.*": {"queue": "outcomes"},
    },
    beat_schedule={},
)
