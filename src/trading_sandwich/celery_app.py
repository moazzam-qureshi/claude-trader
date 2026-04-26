"""Celery application instance, shared by all workers and beat."""
from __future__ import annotations

from pathlib import Path

import yaml
from celery import Celery
from celery.signals import worker_process_init

from trading_sandwich.config import get_settings
from trading_sandwich.logging import configure_logging


def _universe_symbols() -> list[str]:
    """Read tradeable universe from policy.yaml. Returns flat list across
    core+watchlist+observation tiers (excluded tier is not polled).

    Local helper so celery_app.py doesn't import trading_sandwich._universe
    (which would create a circular import chain).
    """
    try:
        with open(Path("policy.yaml")) as f:
            data = yaml.safe_load(f)
        # Phase 2.7+: universe is tiered. Flatten core+watchlist+observation.
        u = data.get("universe", {})
        if isinstance(u, dict) and "tiers" in u:
            symbols: list[str] = []
            for tier in ("core", "watchlist", "observation"):
                symbols.extend(u["tiers"].get(tier, {}).get("symbols", []))
            return symbols
        # Backwards compat: legacy flat-list format.
        if isinstance(u, list):
            return list(u)
        return ["BTCUSDT", "ETHUSDT"]
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
        "trading_sandwich.ingestor.backfill",
        "trading_sandwich.triage.worker",
        "trading_sandwich.triage.heartbeat",
        "trading_sandwich.execution.proposal_sweeper",
        "trading_sandwich.execution.worker",
        "trading_sandwich.execution.paper_match",
        "trading_sandwich.execution.watchdog",
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
        "trading_sandwich.triage.worker.*": {"queue": "triage"},
        "trading_sandwich.triage.heartbeat.*": {"queue": "triage"},
        "trading_sandwich.execution.proposal_sweeper.*": {"queue": "triage"},
        "trading_sandwich.execution.worker.*": {"queue": "execution"},
        "trading_sandwich.execution.paper_match.*": {"queue": "execution"},
        "trading_sandwich.execution.watchdog.*": {"queue": "execution"},
    },
    beat_schedule={
        # Microstructure pollers — one entry per (symbol x task),
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
        "backfill_scan_gaps": {
            "task": "trading_sandwich.ingestor.backfill.scan_gaps",
            "schedule": 300.0,
        },
        "expire_stale_proposals": {
            "task": "trading_sandwich.execution.proposal_sweeper.sweep",
            "schedule": 60.0,
        },
        "paper_match_orders": {
            "task": "trading_sandwich.execution.paper_match.match",
            "schedule": 15.0,
        },
        "reconcile_positions": {
            "task": "trading_sandwich.execution.watchdog.reconcile",
            "schedule": 60.0,
        },
        # Phase 2.7 — heartbeat trader. Fires every 15 min (the min pacing
        # interval). The task itself reads STATE.md and decides whether to
        # actually spawn Claude or skip.
        "heartbeat_tick": {
            "task": "trading_sandwich.triage.heartbeat.heartbeat_tick_celery",
            "schedule": 15 * 60.0,
        },
        # Discord notifier retry sweeper for unposted universe events.
        "discord_universe_retry": {
            "task": "trading_sandwich.triage.heartbeat.discord_retry_sweep_celery",
            "schedule": 15 * 60.0,
        },
        # Daily summary card (Phase 2.7): operator-facing recap, fires every
        # 24h. Scheduled here as a fixed-interval rather than at-midnight
        # because RedBeat's cron support is limited and an offset within the
        # day doesn't matter much for a summary card.
        "discord_daily_summary": {
            "task": "trading_sandwich.triage.heartbeat.daily_summary_celery",
            "schedule": 24 * 60 * 60.0,
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
    from trading_sandwich._metrics_port import allocate_port
    from trading_sandwich.metrics import start_metrics_server
    hostname = (sender.hostname if sender and getattr(sender, "hostname", None) else "") or ""
    port = allocate_port(hostname.split("@")[0])
    start_metrics_server(port)
