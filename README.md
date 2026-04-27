# stealth-browser-use-mcp

AI-native stealth browser MCP server for any MCP-compatible AI agent.

Combines [Browser Use](https://github.com/browser-use/browser-use) (AI agent that understands pages visually) with [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) (Playwright fork with bot detection patches).

**What this solves:**
- Site layout changes break your automation? Browser Use navigates by intent, not selectors.
- Getting blocked by bot detection? Patchright patches Chromium at binary level.

## Quick Start

### Install

```bash
pip install stealth-browser-use-mcp
```

Patchright's Chromium is installed automatically on first use. To pre-install:

```bash
patchright install chromium
```

### Connect to Your AI Agent

<details>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add stealth-browser -- stealth-browser-use-mcp
```

Or add to `.mcp.json`:

```json
{
  "mcpServers": {
    "stealth-browser": {
      "command": "stealth-browser-use-mcp",
      "env": {
        "ANTHROPIC_API_KEY": "your-api-key-here",
        "HEADLESS": "true"
      }
    }
  }
}
```

</details>

<details>
<summary><b>Cursor / Windsurf / VS Code (Copilot)</b></summary>

Add to your MCP settings (`.cursor/mcp.json`, `.windsurf/mcp.json`, or VS Code MCP config):

```json
{
  "mcpServers": {
    "stealth-browser": {
      "command": "stealth-browser-use-mcp",
      "env": {
        "ANTHROPIC_API_KEY": "your-api-key-here",
        "HEADLESS": "true"
      }
    }
  }
}
```

</details>

<details>
<summary><b>Cline / Roo Code</b></summary>

Add via Cline MCP settings:

```json
{
  "mcpServers": {
    "stealth-browser": {
      "command": "stealth-browser-use-mcp",
      "env": {
        "ANTHROPIC_API_KEY": "your-api-key-here",
        "HEADLESS": "true"
      }
    }
  }
}
```

</details>

<details>
<summary><b>OpenCode / Codex / Other MCP Clients</b></summary>

Any MCP client that supports stdio transport can connect:

```bash
stealth-browser-use-mcp
```

Set environment variables before launching:

```bash
export ANTHROPIC_API_KEY="your-api-key-here"
export HEADLESS=true
stealth-browser-use-mcp
```

</details>

### Use

Ask your AI agent to use the stealth browser:

```
"Go to example.com and extract all article titles"
"Search Google for 'best MCP servers' and summarize the top 5 results"
```

## Tools

| Tool | Description |
|------|-------------|
| `browse` | Execute any browser task in natural language |
| `extract` | Pull structured data from a page |

Both tools accept a `max_steps` parameter (capped at 50) to limit interaction depth.

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Required (or `OPENAI_API_KEY`) |
| `BROWSER_USE_MODEL` | `claude-sonnet-4-20250514` / `gpt-4o` | LLM for the browser agent (auto-defaults per provider) |
| `HEADLESS` | `true` | Run browser headless |

For OpenAI models:

```bash
pip install 'stealth-browser-use-mcp[openai]'
```

## How It Works

```
AI Agent  ->  MCP Server (stdio)  ->  Browser Use Agent  ->  Patchright Chromium
 (task)      (this project)          (AI navigation)       (stealth browser)
```

1. You describe a task in natural language
2. Browser Use's AI agent sees the page (screenshot + DOM) and decides actions
3. Patchright's patched Chromium executes actions without triggering bot detection
4. Results come back as text

**Why not just Playwright MCP?**
- Playwright MCP uses CSS selectors — breaks when sites change
- Playwright's Chromium is detected by Cloudflare, DataDome, etc.
- This project uses AI vision — resilient to layout changes
- Patchright patches CDP leaks — passes basic-to-medium bot detection

## Compatibility

Works with any AI agent that supports the [Model Context Protocol](https://modelcontextprotocol.io):

- Claude Code, Claude Desktop
- Cursor, Windsurf, VS Code (Copilot)
- Cline, Roo Code, Continue
- OpenCode, Codex CLI
- Any MCP-compatible client (stdio transport)

## Security

- URL validation: only `http://` and `https://` schemes allowed (blocks `file://`, SSRF)
- Step limits: `max_steps` capped at 50 server-side to prevent billing exhaustion
- Input length limits: task and description capped at 4000 characters
- No secrets in source code — all keys via environment variables

## Limitations

- Binary-level stealth only — does not fix `Runtime.enable` CDP leak at protocol level
- Enterprise-grade WAFs (Cloudflare Turnstile interstitial, DataDome behavioral) may still block without residential proxies
- Each tool call launches a fresh browser (clean state, but ~3s startup)
- Requires an LLM API key (Browser Use agent needs an LLM to reason)

## License

MIT
