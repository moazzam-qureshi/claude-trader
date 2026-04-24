"""Celery application instance, shared by all workers and beat."""
from __future__ import annotations

from pathlib import Path

import yaml
from celery import Celery
from celery.signals import worker_process_init

from trading_sandwich.config import get_settings
from trading_sandwich.logging import configure_logging


def _universe_symbols() -> list[str]:
    """Read universe from policy.yaml. Local helper so celery_app.py doesn't
    import trading_sandwich._universe (which would create a circular import
    chain once _universe grows).
    """
    try:
        with open(Path("policy.yaml")) as f:
            return list(yaml.safe_load(f)["universe"])
    except FileNotFoundError:
        return ["BTCUSDT", "ETHUSDT"]


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
        "trading_sandwich.ingestor.rest_tasks",
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
    beat_schedule={
        # Microstructure pollers — one entry per (symbol × task),
        # expanded at import time from policy.yaml.
        **{
            f"poll_funding_{s}": {
                "task": "trading_sandwich.ingestor.rest_tasks.poll_funding",
                "schedule": 60.0,
                "args": [s],
            }
            for s in _universe_symbols()
        },
        **{
            f"poll_oi_{s}": {
                "task": "trading_sandwich.ingestor.rest_tasks.poll_open_interest",
                "schedule": 300.0,
                "args": [s],
            }
            for s in _universe_symbols()
        },
        **{
            f"poll_lsr_{s}": {
                "task": "trading_sandwich.ingestor.rest_tasks.poll_long_short_ratio",
                "schedule": 300.0,
                "args": [s],
            }
            for s in _universe_symbols()
        },
    },
    beat_scheduler="redbeat.RedBeatScheduler",
    redbeat_redis_url=settings.celery_broker_url.rsplit("/", 1)[0] + "/2",
    redbeat_lock_timeout=300,
)


@worker_process_init.connect
def _init_metrics_server(sender=None, **kwargs) -> None:
    """Each Celery worker process exposes its own /metrics on a fixed port
    chosen by queue name. Prometheus scrape config hits `<service>:<port>`.
    """
    from trading_sandwich.metrics import start_metrics_server

    hostname = (sender.hostname if sender and getattr(sender, "hostname", None) else "") or ""
    port = {"features": 9101, "signals": 9102, "outcomes": 9103}.get(hostname.split("@")[0], 0)
    start_metrics_server(port)
