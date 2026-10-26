from app.config import Settings


def test_run_embedded_workers_bool_from_string():
    s = Settings(
        _env_file=None,
        run_embedded_workers="false",
        db_password="x",
    )
    assert s.run_embedded_workers is False
    s2 = Settings(
        _env_file=None,
        run_embedded_workers="true",
        db_password="x",
    )
    assert s2.run_embedded_workers is True
