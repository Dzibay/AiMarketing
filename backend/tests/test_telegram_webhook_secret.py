from app.telegram_notify import verify_webhook_secret


def test_webhook_secret_open_when_unset(monkeypatch):
    monkeypatch.setattr("app.telegram_notify.settings.telegram_webhook_secret", "")
    assert verify_webhook_secret(None) is True
    assert verify_webhook_secret("anything") is True


def test_webhook_secret_required(monkeypatch):
    monkeypatch.setattr("app.telegram_notify.settings.telegram_webhook_secret", "s3cret")
    assert verify_webhook_secret("s3cret") is True
    assert verify_webhook_secret("wrong") is False
    assert verify_webhook_secret(None) is False
