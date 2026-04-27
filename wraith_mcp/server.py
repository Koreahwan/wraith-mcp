"""Wraith MCP — AI-native stealth browser with bot detection evasion."""

import asyncio
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


@mcp.tool()
async def browse(task: str, url: str | None = None, max_steps: int = 25) -> str:
    """Execute a browser task described in natural language.
    The AI agent navigates and interacts with pages automatically.
    Resilient to site layout changes — no CSS selectors needed.

    Args:
        task: What to do, e.g. "Search for 'AI news' and return top 3 results"
        url: Optional starting URL to navigate to first
        max_steps: Maximum interaction steps (capped at 50)
    """
    if len(task) > _MAX_INPUT_LENGTH:
        raise ValueError(f"Task too long ({len(task)} chars). Max {_MAX_INPUT_LENGTH}.")

    full_task = task
    if url:
        full_task = f"Go to {_validate_url(url)}. Then: {task}"

    session = BrowserSession(browser_profile=_profile())
    async with session:
        agent = Agent(task=full_task, llm=_llm(), browser_session=session)
        async with asyncio.timeout(_TASK_TIMEOUT):
            result = await agent.run(max_steps=_clamp_steps(max_steps))
        return result.final_result() or "Task completed, no text result."


@mcp.tool()
async def extract(url: str, data_description: str, max_steps: int = 15) -> str:
    """Extract structured data from a webpage using natural language.

    Args:
        url: Target URL
        data_description: What to extract, e.g. "all product names and prices in JSON"
        max_steps: Maximum interaction steps (capped at 50)
    """
    if len(data_description) > _MAX_INPUT_LENGTH:
        raise ValueError(
            f"Description too long ({len(data_description)} chars). Max {_MAX_INPUT_LENGTH}."
        )

    task = (
        f"Go to {_validate_url(url)}. "
        f"Extract the following data and return it in a structured format: {data_description}"
    )

    session = BrowserSession(browser_profile=_profile())
    async with session:
        agent = Agent(task=task, llm=_llm(), browser_session=session)
        async with asyncio.timeout(_TASK_TIMEOUT):
            result = await agent.run(max_steps=_clamp_steps(max_steps))
        return result.final_result() or "No data extracted."


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
