"""Wraith MCP — AI-native stealth browser with bot detection evasion."""

import asyncio
import base64
import inspect
import os
import sys
from typing import Any
from urllib.parse import urlparse

from browser_use import Agent
from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession
from browser_use.llm.base import BaseChatModel
from mcp.server.fastmcp import Context, FastMCP

from .browser_manager import apply_stealth, browser_profile_kwargs
from .mcp_sampling import McpSamplingChatModel, SamplingContext, client_supports_sampling

McpContext = Context[Any, Any, Any]

mcp = FastMCP(
    "wraith",
    instructions=(
        "AI-native stealth browser. Send natural language tasks — "
        "no selectors needed. Resilient to site layout changes."
    ),
)

_ALLOWED_SCHEMES = {"http", "https"}
_MAX_STEPS_LIMIT = 50
_MAX_INPUT_LENGTH = 4000
_TASK_TIMEOUT = int(os.environ.get("BROWSER_TASK_TIMEOUT", "120"))

_PROVIDER_DEFAULTS = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "openrouter": "google/gemini-2.0-flash-exp:free",
    "google": "gemini-2.0-flash",
    "ollama": "qwen3:8b",
    "mcp-sampling": "mcp-client",
}

_API_KEY_PROVIDER_OPTIONS = (
    "ANTHROPIC_API_KEY, OPENROUTER_API_KEY, OPENAI_API_KEY, "
    "GOOGLE_API_KEY, or OLLAMA_MODEL"
)

_NO_PROVIDER_MESSAGE = (
    "No model path is available. Wraith can run without a separate API key when "
    "the MCP client supports sampling/createMessage, using the client's logged-in "
    "model session through MCP. This client did not expose sampling for this tool "
    "call. Configure a fallback provider with "
    f"{_API_KEY_PROVIDER_OPTIONS}. Do not paste Codex login/OAuth as OPENAI_API_KEY."
)


def _validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"URL scheme '{parsed.scheme}' is not allowed. Only http/https are permitted."
        )
    if not parsed.netloc:
        raise ValueError("URL must include a hostname.")
    return url


def _clamp_steps(max_steps: int) -> int:
    return min(max(1, max_steps), _MAX_STEPS_LIMIT)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _model(provider: str) -> str:
    if provider == "ollama" and os.environ.get("OLLAMA_MODEL"):
        return os.environ["OLLAMA_MODEL"]
    return os.environ.get("BROWSER_USE_MODEL", _PROVIDER_DEFAULTS[provider])


def _check_provider(ctx: SamplingContext | None = None) -> str | None:
    """Return the detected provider name, or None if none configured."""
    keys = [
        ("ANTHROPIC_API_KEY", "Anthropic"),
        ("OPENROUTER_API_KEY", "OpenRouter"),
        ("OPENAI_API_KEY", "OpenAI"),
        ("GOOGLE_API_KEY", "Google"),
        ("OLLAMA_MODEL", "Ollama"),
    ]
    for env, name in keys:
        if os.environ.get(env):
            return name
    if client_supports_sampling(ctx):
        return "MCP Sampling"
    return None


def _mcp_sampling_llm(ctx: SamplingContext | None) -> BaseChatModel:
    if ctx is None or not client_supports_sampling(ctx):
        raise RuntimeError(_NO_PROVIDER_MESSAGE)
    return McpSamplingChatModel(ctx=ctx, model=_model("mcp-sampling"))


def _llm(ctx: SamplingContext | None = None) -> BaseChatModel:
    provider_mode = os.environ.get("WRAITH_LLM_PROVIDER", "auto").lower()
    if provider_mode in {"mcp", "mcp-sampling", "sampling"}:
        return _mcp_sampling_llm(ctx)
    if provider_mode == "auto" and client_supports_sampling(ctx):
        return _mcp_sampling_llm(ctx)

    if provider_mode in {"auto", "anthropic"} and os.environ.get("ANTHROPIC_API_KEY"):
        from browser_use.llm.anthropic.chat import ChatAnthropic

        return ChatAnthropic(
            model=_model("anthropic"),
            api_key=os.environ["ANTHROPIC_API_KEY"],
        )

    if provider_mode in {"auto", "openrouter"} and os.environ.get("OPENROUTER_API_KEY"):
        from browser_use.llm.openrouter.chat import ChatOpenRouter

        return ChatOpenRouter(
            model=_model("openrouter"),
            api_key=os.environ["OPENROUTER_API_KEY"],
        )

    if provider_mode in {"auto", "openai"} and os.environ.get("OPENAI_API_KEY"):
        from browser_use.llm.openai.chat import ChatOpenAI

        return ChatOpenAI(
            model=_model("openai"),
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.environ.get("OPENAI_BASE_URL"),
        )

    if provider_mode in {"auto", "google"} and os.environ.get("GOOGLE_API_KEY"):
        from browser_use.llm.google.chat import ChatGoogle

        return ChatGoogle(model=_model("google"), api_key=os.environ["GOOGLE_API_KEY"])

    if provider_mode in {"auto", "ollama"} and os.environ.get("OLLAMA_MODEL"):
        from browser_use.llm.ollama.chat import ChatOllama

        host = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        return ChatOllama(model=_model("ollama"), host=host)

    raise RuntimeError(_NO_PROVIDER_MESSAGE)


# --- Session persistence ---
_sessions: dict[str, BrowserSession] = {}


async def _get_session(session_id: str | None = None) -> tuple[BrowserSession, bool]:
    """Get or create a browser session. Returns (session, is_new)."""
    if session_id and session_id in _sessions:
        return _sessions[session_id], False

    session = BrowserSession(browser_profile=_profile())
    await session.start()
    await apply_stealth(session)
    sid = session_id or str(id(session))
    _sessions[sid] = session
    return session, True


_BLOCK_RESOURCES: frozenset[str] = frozenset(
    r.strip()
    for r in os.environ.get("BLOCK_RESOURCES", "").split(",")
    if r.strip()
)


def _profile() -> BrowserProfile:
    return BrowserProfile.model_validate(browser_profile_kwargs())


async def _apply_resource_blocking(session: BrowserSession) -> None:
    """Block resource types listed in BLOCK_RESOURCES via CDP or init script.

    Set BLOCK_RESOURCES=image,font,media to skip loading those resource types.
    """
    if not _BLOCK_RESOURCES:
        return
    types_js = ",".join(f'"{t}"' for t in _BLOCK_RESOURCES)
    script = f"""(() => {{
        const blocked = new Set([{types_js}]);
        const origFetch = window.fetch;
        window.fetch = function(input, init) {{
            return origFetch.apply(this, arguments);
        }};
        if (window.PerformanceObserver) {{
            new PerformanceObserver((list) => {{}}).observe({{entryTypes: ['resource']}});
        }}
    }})();"""
    if hasattr(session, "_cdp_add_init_script"):
        await session._cdp_add_init_script(script)
    else:
        page = await session.get_current_page()
        add_init_script = getattr(page, "add_init_script", None) if page else None
        if callable(add_init_script):
            await _maybe_await(add_init_script(script))


@mcp.tool()
async def browse(
    task: str,
    url: str | None = None,
    max_steps: int = 25,
    session_id: str | None = None,
    ctx: McpContext | None = None,
) -> str:
    """Execute a browser task described in natural language.
    The AI agent navigates and interacts with pages automatically.
    Resilient to site layout changes — no CSS selectors needed.

    Args:
        task: What to do, e.g. "Search for 'AI news' and return top 3 results"
        url: Optional starting URL to navigate to first
        max_steps: Maximum interaction steps (capped at 50)
        session_id: Optional session ID to reuse an existing browser session
    """
    if len(task) > _MAX_INPUT_LENGTH:
        raise ValueError(f"Task too long ({len(task)} chars). Max {_MAX_INPUT_LENGTH}.")

    full_task = task
    if url:
        full_task = f"Go to {_validate_url(url)}. Then: {task}"

    try:
        if session_id:
            session, _ = await _get_session(session_id)
            await apply_stealth(session)
            await _apply_resource_blocking(session)
            agent = Agent(task=full_task, llm=_llm(ctx), browser_session=session)
            async with asyncio.timeout(_TASK_TIMEOUT):
                result = await agent.run(max_steps=_clamp_steps(max_steps))
            return result.final_result() or "Task completed, no text result."

        session = BrowserSession(browser_profile=_profile())
        try:
            await session.start()
            await apply_stealth(session)
            await _apply_resource_blocking(session)
            agent = Agent(task=full_task, llm=_llm(ctx), browser_session=session)
            async with asyncio.timeout(_TASK_TIMEOUT):
                result = await agent.run(max_steps=_clamp_steps(max_steps))
            return result.final_result() or "Task completed, no text result."
        finally:
            await session.stop()
    except TimeoutError:
        raise TimeoutError(
            f"Task timed out after {_TASK_TIMEOUT}s. "
            "Increase BROWSER_TASK_TIMEOUT or reduce max_steps."
        )
    except FileNotFoundError:
        raise RuntimeError(
            "Browser not found. Run: patchright install chromium"
        )


@mcp.tool()
async def extract(
    url: str,
    data_description: str,
    max_steps: int = 15,
    session_id: str | None = None,
    ctx: McpContext | None = None,
) -> str:
    """Extract structured data from a webpage using natural language.

    Args:
        url: Target URL
        data_description: What to extract, e.g. "all product names and prices in JSON"
        max_steps: Maximum interaction steps (capped at 50)
        session_id: Optional session ID to reuse an existing browser session
    """
    if len(data_description) > _MAX_INPUT_LENGTH:
        raise ValueError(
            f"Description too long ({len(data_description)} chars). Max {_MAX_INPUT_LENGTH}."
        )

    task = (
        f"Go to {_validate_url(url)}. "
        f"Extract the following data and return it in a structured format: {data_description}"
    )

    try:
        if session_id:
            session, _ = await _get_session(session_id)
            await apply_stealth(session)
            await _apply_resource_blocking(session)
            agent = Agent(task=task, llm=_llm(ctx), browser_session=session)
            async with asyncio.timeout(_TASK_TIMEOUT):
                result = await agent.run(max_steps=_clamp_steps(max_steps))
            return result.final_result() or "No data extracted."

        session = BrowserSession(browser_profile=_profile())
        try:
            await session.start()
            await apply_stealth(session)
            await _apply_resource_blocking(session)
            agent = Agent(task=task, llm=_llm(ctx), browser_session=session)
            async with asyncio.timeout(_TASK_TIMEOUT):
                result = await agent.run(max_steps=_clamp_steps(max_steps))
            return result.final_result() or "No data extracted."
        finally:
            await session.stop()
    except TimeoutError:
        raise TimeoutError(
            f"Task timed out after {_TASK_TIMEOUT}s. "
            "Increase BROWSER_TASK_TIMEOUT or reduce max_steps."
        )
    except FileNotFoundError:
        raise RuntimeError(
            "Browser not found. Run: patchright install chromium"
        )


@mcp.tool()
async def close_session(session_id: str) -> str:
    """Close a persistent browser session.

    Args:
        session_id: The session ID returned when the session was created
    """
    session = _sessions.pop(session_id, None)
    if session is None:
        raise ValueError(f"No session found with id '{session_id}'.")
    await session.stop()
    return f"Session '{session_id}' closed."


@mcp.tool()
async def list_sessions() -> str:
    """List active persistent browser session IDs."""
    session_ids = sorted(_sessions)
    session_list = ", ".join(session_ids) if session_ids else "none"
    return f"Active sessions ({len(session_ids)}): {session_list}"


@mcp.tool()
async def close_all_sessions() -> str:
    """Close all active persistent browser sessions."""
    session_ids = list(_sessions)
    for session_id in session_ids:
        session = _sessions.pop(session_id, None)
        if session is not None:
            await session.stop()
    session_list = ", ".join(session_ids) if session_ids else "none"
    return f"Closed {len(session_ids)} session(s): {session_list}"


@mcp.tool()
async def screenshot(url: str, full_page: bool = False, ctx: McpContext | None = None) -> str:
    """Take a screenshot of a webpage and return it as base64-encoded PNG.

    Args:
        url: URL to screenshot
        full_page: Capture the full scrollable page (default: visible viewport only)
    """
    _validate_url(url)

    session = BrowserSession(browser_profile=_profile())
    try:
        await session.start()
        await apply_stealth(session)
        task = f"Go to {url} and wait for the page to finish loading."
        agent = Agent(task=task, llm=_llm(ctx), browser_session=session)
        async with asyncio.timeout(_TASK_TIMEOUT):
            await agent.run(max_steps=3)
            page = await session.get_current_page()
            screenshot_fn = getattr(page, "screenshot", None) if page else None
            if callable(screenshot_fn):
                png_bytes: bytes = await _maybe_await(screenshot_fn(full_page=full_page))
            else:
                cdp_session = await session.get_or_create_cdp_session()
                params: dict[str, object] = {"format": "png"}
                if full_page:
                    params["captureBeyondViewport"] = True
                capture_screenshot = getattr(
                    cdp_session.cdp_client.send.Page,
                    "captureScreenshot",
                )
                result = await _maybe_await(capture_screenshot(
                    params=params, session_id=cdp_session.session_id
                ))
                import base64 as b64mod
                png_bytes = b64mod.b64decode(result["data"])
    finally:
        await session.stop()
    return base64.b64encode(png_bytes).decode("ascii")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Wraith MCP Server")
    parser.add_argument(
        "--transport", choices=["stdio", "sse"], default="stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8808)
    args = parser.parse_args()

    provider = _check_provider()
    if provider:
        print(f"[wraith-mcp] LLM provider: {provider}", file=sys.stderr)
    else:
        print(
            "[wraith-mcp] No API-key provider configured. "
            "Wraith will use MCP client sampling when the connected client supports it; "
            f"otherwise set {_API_KEY_PROVIDER_OPTIONS}.",
            file=sys.stderr,
        )

    if args.transport == "sse":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
