from datetime import UTC, datetime, timedelta

from trading_sandwich.ingestor.backfill import expected_candle_opens


def test_expected_opens_1m():
    start = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
    end = datetime(2026, 4, 21, 12, 5, tzinfo=UTC)
    opens = expected_candle_opens(start, end, "1m")
    assert opens == [start + timedelta(minutes=i) for i in range(5)]


def test_expected_opens_5m():
    start = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
    end = datetime(2026, 4, 21, 12, 20, tzinfo=UTC)
    opens = expected_candle_opens(start, end, "5m")
    assert opens == [start + timedelta(minutes=5 * i) for i in range(4)]
