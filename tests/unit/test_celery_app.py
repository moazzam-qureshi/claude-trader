from trading_sandwich.celery_app import app


def test_celery_app_configured():
    assert app.main == "trading_sandwich"
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.worker_prefetch_multiplier == 1


def test_celery_beat_schedule_has_placeholders():
    assert isinstance(app.conf.beat_schedule, dict)


def test_redbeat_scheduler_class_set():
    assert app.conf.beat_scheduler == "redbeat.RedBeatScheduler"


def test_redbeat_redis_url_distinct():
    # redbeat keys go into Redis db 2 — distinct from broker (db 0) and backend (db 1)
    assert app.conf.redbeat_redis_url.endswith("/2")
