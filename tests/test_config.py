import pytest
from pydantic import ValidationError

from app.config import Settings

def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)

def test_defaults():
    settings = make_settings()

    assert settings.environment == "dev"   
    assert settings.default_timezone == "Asia/Bangkok"
    assert settings.line_channel_secret is None

def test_reads_environment_variables(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "Asia/Tokyo")

    settings = make_settings()

    assert settings.environment == "prod"
    assert settings.default_timezone == "Asia/Tokyo"

def test_rejects_unknown_environment():
    with pytest.raises(ValidationError):
        make_settings(environment="production")

def test_rejects_unknown_timezone():
    with pytest.raises(ValidationError):
        make_settings(default_timezone="Asia/Bangkokk")

def test_secret_is_hidden_when_printed():
    settings = make_settings(line_channel_secret="super-secret-123")

    assert "super-secret-123" not in repr(settings)
    assert settings.line_channel_secret.get_secret_value() == "super-secret-123"

def test_empty_secret_becomes_none():
    settings = make_settings(line_channel_access_token="")

    assert settings.line_channel_access_token is None