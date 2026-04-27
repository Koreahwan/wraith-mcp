# stealth-browser-use-mcp

AI-native stealth browser MCP server. [Browser Use](https://github.com/browser-use/browser-use) + [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright).

- **Self-healing**: navigates by AI vision, not CSS selectors
- **Stealth**: Patchright patches Chromium to bypass bot detection

## Quick Start

> Add stealth-browser-use-mcp as MCP server and scrape today's top stories from news.ycombinator.com

## Install

```bash
pip install stealth-browser-use-mcp
```

## Setup

Add to your MCP config (`.mcp.json`, `.cursor/mcp.json`, `.windsurf/mcp.json`, etc.):

```json
{
  "mcpServers": {
    "stealth-browser": {
      "command": "stealth-browser-use-mcp",
      "env": {
        "ANTHROPIC_API_KEY": "your-key",
        "HEADLESS": "true"
      }
    }
  }
}
```

Works with any MCP client: Cursor, Windsurf, VS Code, Cline, Roo Code, OpenCode, Codex, and more.

## Tools

| Tool | Description |
|------|-------------|
| `browse` | Execute any browser task in natural language |
| `extract` | Pull structured data from a page |

## LLM Providers

| Provider | Key | Install |
|----------|-----|---------|
| Anthropic (default) | `ANTHROPIC_API_KEY` | included |
| OpenAI | `OPENAI_API_KEY` | `[openai]` |
| DeepSeek / Groq / Together | `OPENAI_API_KEY` + `OPENAI_BASE_URL` | `[openai]` |
| Google Gemini | `GOOGLE_API_KEY` | `[google]` |
| Ollama (local) | `OLLAMA_MODEL` | `[ollama]` |

All providers: `pip install 'stealth-browser-use-mcp[all]'`

Set `BROWSER_USE_MODEL` to override the default model per provider.

## How It Works

```
AI Agent -> MCP Server -> Browser Use Agent -> Patchright Chromium
```

1. Describe a task in natural language
2. Browser Use sees the page (screenshot + DOM) and decides actions
3. Patchright executes without triggering bot detection

## Security

- URL scheme validation (http/https only)
- `max_steps` capped at 50 server-side
- Input length capped at 4000 chars

## Limitations

- Binary-level stealth only (no `Runtime.enable` CDP fix)
- Enterprise WAFs may still block without residential proxies
- Fresh browser per call (~3s startup)
- Requires an LLM API key

## License

MIT
