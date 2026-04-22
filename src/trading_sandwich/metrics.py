"""Prometheus metric definitions and HTTP scrape endpoint starter."""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

CANDLES_INGESTED = Counter(
    "ts_candles_ingested_total",
    "Candles written to raw_candles",
    ["symbol", "timeframe"],
)

FEATURES_COMPUTED = Counter(
    "ts_features_computed_total",
    "Features rows written",
    ["symbol", "timeframe"],
)

FEATURE_COMPUTE_SECONDS = Histogram(
    "ts_feature_compute_seconds",
    "Time to compute a features row",
    ["symbol", "timeframe"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)

SIGNALS_FIRED = Counter(
    "ts_signals_fired_total",
    "Signals emitted",
    ["symbol", "timeframe", "archetype", "gating_outcome"],
)

OUTCOMES_MEASURED = Counter(
    "ts_outcomes_measured_total",
    "Outcome rows written",
    ["horizon"],
)

INGESTOR_WS_RECONNECTS = Counter(
    "ts_ingestor_ws_reconnects_total",
    "WS reconnects observed in the ingestor",
    ["symbol"],
)

QUEUE_DEPTH = Gauge(
    "ts_celery_queue_depth",
    "Celery queue depth (populated by a Beat job in Phase 1)",
    ["queue"],
)


def start_metrics_server(port: int) -> None:
    """Start a Prometheus scrape endpoint. Call once per process."""
    if port > 0:
        start_http_server(port)
