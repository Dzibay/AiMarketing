from app.worker_lock import _key


def test_lock_keys_stable_and_distinct():
    assert _key("stats") == _key("stats")
    assert _key("stats") != _key("rules")
    assert _key("connections") > 0
