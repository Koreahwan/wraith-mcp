import pytest
from wraith_mcp.server import _validate_url, _clamp_steps, _check_provider, _model


class TestValidateUrl:
    def test_http_allowed(self):
        assert _validate_url("http://example.com") == "http://example.com"

    def test_https_allowed(self):
        assert _validate_url("https://example.com") == "https://example.com"

    def test_file_blocked(self):
        with pytest.raises(ValueError, match="not allowed"):
            _validate_url("file:///etc/passwd")

    def test_javascript_blocked(self):
        with pytest.raises(ValueError, match="not allowed"):
            _validate_url("javascript:alert(1)")

    def test_empty_hostname(self):
        with pytest.raises(ValueError, match="hostname"):
            _validate_url("http:///path")


class TestClampSteps:
    def test_normal(self):
        assert _clamp_steps(25) == 25

    def test_over_max(self):
        assert _clamp_steps(100) == 50

    def test_zero(self):
        assert _clamp_steps(0) == 1

    def test_negative(self):
        assert _clamp_steps(-5) == 1


class TestCheckProvider:
    def test_no_provider(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        assert _check_provider() is None

    def test_anthropic(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
        assert _check_provider() == "Anthropic"


class TestModel:
    def test_default_anthropic(self, monkeypatch):
        monkeypatch.delenv("BROWSER_USE_MODEL", raising=False)
        assert "claude" in _model("anthropic")

    def test_override(self, monkeypatch):
        monkeypatch.setenv("BROWSER_USE_MODEL", "custom-model")
        assert _model("anthropic") == "custom-model"
