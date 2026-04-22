from trading_sandwich.metrics import (
    CANDLES_INGESTED,
    FEATURES_COMPUTED,
    OUTCOMES_MEASURED,
    SIGNALS_FIRED,
    start_metrics_server,
)


def test_counters_exist():
    before = CANDLES_INGESTED.labels(symbol="BTCUSDT", timeframe="1m")._value.get()
    CANDLES_INGESTED.labels(symbol="BTCUSDT", timeframe="1m").inc()
    after = CANDLES_INGESTED.labels(symbol="BTCUSDT", timeframe="1m")._value.get()
    assert after == before + 1


def test_features_and_signals_and_outcomes_counters_present():
    FEATURES_COMPUTED.labels(symbol="X", timeframe="1m").inc()
    SIGNALS_FIRED.labels(
        symbol="X", timeframe="1m", archetype="trend_pullback", gating_outcome="below_threshold"
    ).inc()
    OUTCOMES_MEASURED.labels(horizon="15m").inc()


def test_start_metrics_server_is_noop_when_port_zero():
    start_metrics_server(0)
