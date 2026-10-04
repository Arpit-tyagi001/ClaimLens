import pytest
from backend.app.config import get_settings
from backend.app.services.rate_limiter import limiter


@pytest.fixture(autouse=True)
def disable_rate_limit_and_reset(monkeypatch):
    limiter.reset()
    settings = get_settings()
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    yield
    limiter.reset()
