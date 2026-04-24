from trading_sandwich.config import Settings


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("POSTGRES_DB", "d")
    monkeypatch.setenv("POSTGRES_HOST", "h")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://r:6379/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://r:6379/1")
    monkeypatch.setenv("UNIVERSE_SYMBOLS", "BTCUSDT,ETHUSDT")
    monkeypatch.setenv("UNIVERSE_TIMEFRAMES", "1m,5m")

    s = Settings()
    assert s.postgres_user == "u"
    assert s.universe_symbols == ["BTCUSDT", "ETHUSDT"]
    assert s.universe_timeframes == ["1m", "5m"]
    assert s.database_url.startswith("postgresql+asyncpg://")


def test_database_url_composition(monkeypatch):
    monkeypatch.setenv("POSTGRES_USER", "trading")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("POSTGRES_DB", "ts")
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://r:6379/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://r:6379/1")

    s = Settings()
    assert s.database_url == "postgresql+asyncpg://trading:secret@db:5433/ts"


def test_configure_logging_emits(monkeypatch, capsys):
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("POSTGRES_DB", "d")
    monkeypatch.setenv("POSTGRES_HOST", "h")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://r:6379/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://r:6379/1")
    monkeypatch.setenv("LOG_FORMAT", "console")

    import trading_sandwich.config as cfg
    cfg._settings = None
    from trading_sandwich.logging import configure_logging, get_logger

    configure_logging()
    log = get_logger("test")
    log.info("hello", key="value")
    out = capsys.readouterr().out
    assert "hello" in out
    assert "key" in out


def test_pgbouncer_url_composition(monkeypatch):
    monkeypatch.setenv("POSTGRES_USER", "trading")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("POSTGRES_DB", "ts")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("PGBOUNCER_HOST", "pgb")
    monkeypatch.setenv("PGBOUNCER_PORT", "7777")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://r/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://r/1")

    import trading_sandwich.config as cfg
    cfg._settings = None
    s = cfg.Settings()
    assert s.pgbouncer_url == "postgresql+asyncpg://trading:secret@pgb:7777/ts"
