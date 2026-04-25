from decimal import Decimal

from trading_sandwich.mcp.tools.reads import _confidence_bucket


def test_confidence_bucket_low():
    assert _confidence_bucket(Decimal("0.10")) == "low"
    assert _confidence_bucket(Decimal("0.33")) == "low"


def test_confidence_bucket_mid():
    assert _confidence_bucket(Decimal("0.34")) == "mid"
    assert _confidence_bucket(Decimal("0.66")) == "mid"


def test_confidence_bucket_high():
    assert _confidence_bucket(Decimal("0.67")) == "high"
    assert _confidence_bucket(Decimal("0.99")) == "high"
