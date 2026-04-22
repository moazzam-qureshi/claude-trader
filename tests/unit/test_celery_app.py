from trading_sandwich.celery_app import app


def test_celery_app_configured():
    assert app.main == "trading_sandwich"
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.worker_prefetch_multiplier == 1


def test_celery_beat_schedule_has_placeholders():
    assert isinstance(app.conf.beat_schedule, dict)
