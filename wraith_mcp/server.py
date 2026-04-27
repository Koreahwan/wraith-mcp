"""Wraith MCP — AI-native stealth browser with bot detection evasion."""

import asyncio
import base64
import os
import sys
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from browser_use import Agent
from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession
from langchain_core.language_models import BaseChatModel

from .browser_manager import chromium_path

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
}


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


def _model(provider: str) -> str:
    return os.environ.get("BROWSER_USE_MODEL", _PROVIDER_DEFAULTS[provider])


def _check_provider() -> str | None:
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
    return None


def _llm() -> BaseChatModel:
    if os.environ.get("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=_model("anthropic"))

    if os.environ.get("OPENROUTER_API_KEY"):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=_model("openrouter"),
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url="https://openrouter.ai/api/v1",
        )

    if os.environ.get("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI

        kwargs: dict = {"model": _model("openai")}
        if os.environ.get("OPENAI_BASE_URL"):
            kwargs["base_url"] = os.environ["OPENAI_BASE_URL"]
        return ChatOpenAI(**kwargs)

    if os.environ.get("GOOGLE_API_KEY"):
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=_model("google"))

    if os.environ.get("OLLAMA_MODEL"):
        from langchain_ollama import ChatOllama

        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        return ChatOllama(model=_model("ollama"), base_url=base_url)

    raise RuntimeError(
        "No LLM provider configured. Set one of: "
        "ANTHROPIC_API_KEY, OPENROUTER_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY, or OLLAMA_MODEL"
    )


# --- Session persistence ---
_sessions: dict[str, BrowserSession] = {}


async def _get_session(session_id: str | None = None) -> tuple[BrowserSession, bool]:
    """Get or create a browser session. Returns (session, is_new)."""
    if session_id and session_id in _sessions:
        return _sessions[session_id], False

    session = BrowserSession(browser_profile=_profile())
    await session.start()
    sid = session_id or str(id(session))
    _sessions[sid] = session
    return session, True


_STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--force-color-profile=srgb",
    "--disable-features=IsolateOrigins,site-per-process",
]

_IGNORE_DEFAULT_ARGS = [
    "--enable-automation",
    "--disable-extensions",
]


_BLOCK_RESOURCES: frozenset[str] = frozenset(
    r.strip()
    for r in os.environ.get("BLOCK_RESOURCES", "").split(",")
    if r.strip()
)


def _profile() -> BrowserProfile:
    headless = os.environ.get("HEADLESS", "true").lower() == "true"
    proxy = os.environ.get("PROXY_SERVER")
    kwargs: dict = {
        "executable_path": chromium_path(),
        "headless": headless,
        "args": _STEALTH_ARGS,
    }
    if proxy:
        kwargs["proxy"] = {"server": proxy}
    return BrowserProfile(**kwargs)


async def _apply_resource_blocking(session: BrowserSession) -> None:
    """Block resource types listed in BLOCK_RESOURCES via Playwright routing.

    Set BLOCK_RESOURCES=image,font,media to skip loading those resource types.
    This reduces bandwidth and speeds up page loads for scraping/extraction tasks.
    """
    if not _BLOCK_RESOURCES:
        return
    # Access the underlying Playwright page through browser-use's session API
    page = await session.get_current_page()
    if page is None:
        return

    async def _intercept(route) -> None:  # type: ignore[type-arg]
        if route.request.resource_type in _BLOCK_RESOURCES:
            await route.abort()
        else:
            await route.continue_()

    await page.route("**/*", _intercept)


@mcp.tool()
async def browse(
    task: str,
    url: str | None = None,
    max_steps: int = 25,
    session_id: str | None = None,
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

    if session_id:
        session, _ = await _get_session(session_id)
        await _apply_resource_blocking(session)
        agent = Agent(task=full_task, llm=_llm(), browser_session=session)
        async with asyncio.timeout(_TASK_TIMEOUT):
            result = await agent.run(max_steps=_clamp_steps(max_steps))
        return result.final_result() or "Task completed, no text result."

    session = BrowserSession(browser_profile=_profile())
    async with session:
        await _apply_resource_blocking(session)
        agent = Agent(task=full_task, llm=_llm(), browser_session=session)
        async with asyncio.timeout(_TASK_TIMEOUT):
            result = await agent.run(max_steps=_clamp_steps(max_steps))
        return result.final_result() or "Task completed, no text result."


@mcp.tool()
async def extract(
    url: str,
    data_description: str,
    max_steps: int = 15,
    session_id: str | None = None,
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

    if session_id:
        session, _ = await _get_session(session_id)
        await _apply_resource_blocking(session)
        agent = Agent(task=task, llm=_llm(), browser_session=session)
        async with asyncio.timeout(_TASK_TIMEOUT):
            result = await agent.run(max_steps=_clamp_steps(max_steps))
        return result.final_result() or "No data extracted."

    session = BrowserSession(browser_profile=_profile())
    async with session:
        await _apply_resource_blocking(session)
        agent = Agent(task=task, llm=_llm(), browser_session=session)
        async with asyncio.timeout(_TASK_TIMEOUT):
            result = await agent.run(max_steps=_clamp_steps(max_steps))
        return result.final_result() or "No data extracted."


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
async def screenshot(url: str, full_page: bool = False) -> str:
    """Take a screenshot of a webpage and return it as base64-encoded PNG.

    Args:
        url: URL to screenshot
        full_page: Capture the full scrollable page (default: visible viewport only)
    """
    _validate_url(url)

    session = BrowserSession(browser_profile=_profile())
    async with session:
        task = f"Go to {url} and wait for the page to finish loading."
        agent = Agent(task=task, llm=_llm(), browser_session=session)
        async with asyncio.timeout(_TASK_TIMEOUT):
            await agent.run(max_steps=3)
            page = await session.get_current_page()
            png_bytes: bytes = await page.screenshot(full_page=full_page)
    return base64.b64encode(png_bytes).decode("ascii")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Wraith MCP Server")
    parser.add_argument(
        "--transport", choices=["stdio", "sse"], default="stdio",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8808)
    args = parser.parse_args()

    provider = _check_provider()
    if provider:
        print(f"[wraith-mcp] LLM provider: {provider}", file=sys.stderr)
    else:
        print(
            "[wraith-mcp] WARNING: No LLM provider configured. "
            "Set ANTHROPIC_API_KEY, OPENROUTER_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY, or OLLAMA_MODEL",
            file=sys.stderr,
        )

    if args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
