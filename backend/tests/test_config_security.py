import pytest

from app.core.config import validate_secret_key


@pytest.mark.parametrize(
    "secret_key",
    [
        "",
        "short-key",
        "dev-secret-key",
        "change-this-in-production",
        "your-secret-key-change-in-production",
        "your-super-secret-key-change-in-production",
        "<random-string>",
    ],
)
def test_secret_key_rejects_missing_weak_and_placeholder_values(secret_key):
    with pytest.raises(RuntimeError):
        validate_secret_key(secret_key)


def test_secret_key_accepts_long_explicit_value():
    validate_secret_key("a-unique-explicit-test-key-with-at-least-32-characters")
