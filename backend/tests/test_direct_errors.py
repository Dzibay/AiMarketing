from app.yandex import YandexError, direct_api_error


def test_direct_api_error_with_detail():
    exc = direct_api_error(
        {"error": {"error_code": 52, "error_string": "Invalid", "error_detail": "Нет прав"}},
        403,
    )
    assert isinstance(exc, YandexError)
    assert exc.code == "direct-denied"
    assert exc.message == "Нет прав"


def test_direct_api_error_add_results():
    exc = direct_api_error(
        {
            "AddResults": [
                {"Errors": [{"Message": "Фраза уже есть", "Code": 5000}]},
            ]
        },
        200,
    )
    assert exc.code == "direct"
    assert "Фраза" in (exc.message or "")


def test_direct_api_error_fallback():
    exc = direct_api_error({}, 500)
    assert exc.code == "direct"
    assert exc.message is None
