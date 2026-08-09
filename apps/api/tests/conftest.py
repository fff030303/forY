import pytest


@pytest.fixture(autouse=True)
def disable_real_deepseek_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
