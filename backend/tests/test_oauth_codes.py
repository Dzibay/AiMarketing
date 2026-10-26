from app.oauth_codes import consume_oauth_code, issue_oauth_code


def test_oauth_code_roundtrip():
    code = issue_oauth_code(42)
    assert consume_oauth_code(code) == 42
    assert consume_oauth_code(code) is None


def test_oauth_code_invalid():
    assert consume_oauth_code("") is None
    assert consume_oauth_code("nope") is None
