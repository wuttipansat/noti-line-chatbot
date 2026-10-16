from app import worker

def test_tasks_are_registered():
    assert "inbox.process_user" in worker.celery_app.tasks
    assert "inbox.sweep" in worker.celery_app.tasks

def test_sweeper_runs_every_minute():
    schedule = worker.celery_app.conf.beat_schedule["sweep-inbox-every-minute"]

    assert schedule["task"] == "inbox.sweep"
    assert schedule["schedule"] == 60.0

def test_enqueue_inbox_sends_the_user_id(monkeypatch):
    sent = []
    monkeypatch.setattr(worker.process_user_inbox_task, "delay", sent.append)

    worker.enqueue_inbox("U-alice")

    assert sent == ["U-alice"]

def test_outbox_sweeper_runs_every_15_seconds():
    schedule = worker.celery_app.conf.beat_schedule["send-due-outbox-every-15s"]

    assert schedule["task"] == "outbox.sweep"
    assert schedule["schedule"] == 15.0