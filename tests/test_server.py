import asyncio
from typing import Any, cast

import pytest
from browser_use.browser.session import BrowserSession
from mcp import types as mcp_types
from pydantic import BaseModel
from wraith_mcp.mcp_sampling import McpSamplingChatModel
from wraith_mcp.server import (
    _check_provider,
    _clamp_steps,
    _llm,
    _model,
    _sessions,
    _validate_url,
    close_all_sessions,
    list_sessions,
)


PROVIDER_ENVS = (
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "OLLAMA_MODEL",
)


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
        for env in PROVIDER_ENVS:
            monkeypatch.delenv(env, raising=False)
        assert _check_provider() is None

    @pytest.mark.parametrize(
        ("env", "provider"),
        [
            ("ANTHROPIC_API_KEY", "Anthropic"),
            ("OPENROUTER_API_KEY", "OpenRouter"),
            ("OPENAI_API_KEY", "OpenAI"),
            ("GOOGLE_API_KEY", "Google"),
            ("OLLAMA_MODEL", "Ollama"),
        ],
    )
    def test_detects_provider(self, monkeypatch, env, provider):
        for provider_env in PROVIDER_ENVS:
            monkeypatch.delenv(provider_env, raising=False)
        monkeypatch.setenv(env, "test")
        assert _check_provider() == provider

    def test_detects_mcp_sampling(self, monkeypatch):
        for env in PROVIDER_ENVS:
            monkeypatch.delenv(env, raising=False)
        assert _check_provider(FakeContext()) == "MCP Sampling"


class TestLlm:
    def test_no_provider_message_explains_sampling_fallback(self, monkeypatch):
        for env in PROVIDER_ENVS:
            monkeypatch.delenv(env, raising=False)

        with pytest.raises(RuntimeError) as exc:
            _llm()

        message = str(exc.value)
        assert "supports sampling/createMessage" in message
        assert "fallback provider" in message
        assert "Do not paste Codex login/OAuth as OPENAI_API_KEY" in message

    def test_uses_mcp_sampling_without_api_key(self, monkeypatch):
        for env in PROVIDER_ENVS:
            monkeypatch.delenv(env, raising=False)
        assert _llm(FakeContext()).provider == "mcp-sampling"

    def test_auto_prefers_mcp_sampling_over_api_key(self, monkeypatch):
        for env in PROVIDER_ENVS:
            monkeypatch.delenv(env, raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        assert _llm(FakeContext()).provider == "mcp-sampling"

    def test_falls_back_to_api_key_without_sampling(self, monkeypatch):
        for env in PROVIDER_ENVS:
            monkeypatch.delenv(env, raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "test")
        assert _llm().provider == "openrouter"

    def test_forces_api_key_provider_when_requested(self, monkeypatch):
        for env in PROVIDER_ENVS:
            monkeypatch.delenv(env, raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        monkeypatch.setenv("WRAITH_LLM_PROVIDER", "openai")
        assert _llm(FakeContext()).provider == "openai"


class TestModel:
    def test_default_anthropic(self, monkeypatch):
        monkeypatch.delenv("BROWSER_USE_MODEL", raising=False)
        assert "claude" in _model("anthropic")

    def test_override(self, monkeypatch):
        monkeypatch.setenv("BROWSER_USE_MODEL", "custom-model")
        assert _model("anthropic") == "custom-model"

    def test_ollama_model_uses_ollama_env(self, monkeypatch):
        monkeypatch.delenv("BROWSER_USE_MODEL", raising=False)
        monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")
        assert _model("ollama") == "qwen2.5:7b"


class SamplingAnswer(BaseModel):
    answer: str


class FakeClientParams:
    capabilities = mcp_types.ClientCapabilities(
        sampling=mcp_types.SamplingCapability(),
    )


class FakeSession:
    client_params: FakeClientParams = FakeClientParams()

    def __init__(self, text: str = '{"answer":"ok"}') -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []
        self.stopped = False

    async def create_message(self, **kwargs: Any) -> mcp_types.CreateMessageResult:
        self.calls.append(kwargs)
        return mcp_types.CreateMessageResult(
            role="assistant",
            content=mcp_types.TextContent(type="text", text=self.text),
            model="fake-client-model",
            stopReason="endTurn",
        )

    async def stop(self) -> None:
        self.stopped = True


class FakeRequestContext:
    request_id: str = "req-1"


class FakeContext:
    def __init__(self, session: FakeSession | None = None) -> None:
        self.session = session or FakeSession()
        self.request_context = FakeRequestContext()


def _as_browser_session(session: FakeSession) -> BrowserSession:
    return cast(BrowserSession, cast(object, session))


class TestMcpSamplingChatModel:
    def test_invokes_client_sampling(self):
        from browser_use.llm.messages import SystemMessage, UserMessage

        ctx = FakeContext(FakeSession("hello"))
        model = McpSamplingChatModel(ctx=ctx)

        result = asyncio.run(
            model.ainvoke([
                SystemMessage(content="system"),
                UserMessage(content="user"),
            ])
        )

        assert result.completion == "hello"
        call = ctx.session.calls[0]
        assert call["system_prompt"] == "system"
        assert call["messages"][0].content.text == "user"
        assert call["include_context"] == "none"

    def test_invokes_client_sampling_with_structured_output(self):
        ctx = FakeContext(FakeSession('{"answer":"ok"}'))
        model = McpSamplingChatModel(ctx=ctx)

        result = asyncio.run(model.ainvoke([], output_format=SamplingAnswer))

        assert result.completion.answer == "ok"
        assert "Return only valid JSON" in ctx.session.calls[0]["system_prompt"]

    def test_redacts_image_url_query_from_sampling_prompt(self):
        from browser_use.llm.messages import ContentPartImageParam, ImageURL, UserMessage

        ctx = FakeContext(FakeSession("ok"))
        model = McpSamplingChatModel(ctx=ctx)
        image = ContentPartImageParam(
            image_url=ImageURL(
                url="https://example.com/image.png?token=secret#frag",
            )
        )

        asyncio.run(model.ainvoke([UserMessage(content=[image])]))

        content = ctx.session.calls[0]["messages"][0].content
        assert content.text == "[Image URL: https://example.com/image.png [query and fragment redacted]]"


class TestSessionManagementTools:
    def test_list_sessions_returns_active_ids_and_count(self):
        original_sessions = dict(_sessions)
        try:
            _sessions.clear()
            first_session = FakeSession()
            second_session = FakeSession()
            _sessions["alpha"] = _as_browser_session(first_session)
            _sessions["beta"] = _as_browser_session(second_session)

            result = asyncio.run(list_sessions())

            assert result == "Active sessions (2): alpha, beta"
        finally:
            _sessions.clear()
            _sessions.update(original_sessions)

    def test_close_all_sessions_closes_every_session_and_clears_state(self):
        original_sessions = dict(_sessions)
        try:
            first_session = FakeSession()
            second_session = FakeSession()
            _sessions.clear()
            _sessions["alpha"] = _as_browser_session(first_session)
            _sessions["beta"] = _as_browser_session(second_session)

            result = asyncio.run(close_all_sessions())

            assert result == "Closed 2 session(s): alpha, beta"
            assert first_session.stopped is True
            assert second_session.stopped is True
            assert _sessions == {}
        finally:
            _sessions.clear()
            _sessions.update(original_sessions)

    def test_close_all_sessions_on_empty_state(self):
        original_sessions = dict(_sessions)
        try:
            _sessions.clear()

            result = asyncio.run(close_all_sessions())

            assert result == "Closed 0 session(s): none"
            assert _sessions == {}
        finally:
            _sessions.clear()
            _sessions.update(original_sessions)
