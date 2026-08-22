# Mnemos MCP presets — one-line configs for major harnesses

**🌐 Language / Язык:** English · [Русский](../docs/ru/user/integration-guide.md#однострочные-mcp-пресеты)

One line (or one paste block) per harness. Every preset connects the harness to
the Mnemos MCP server over stdio — the same wire ADR-0017 D1 standardised on:
command `mnemos`, args `["mcp-server"]`.

**Prerequisite:** `mnemos` is on `PATH`:

```bash
curl -fsSL https://raw.githubusercontent.com/Korrnals/mnemos/main/scripts/install.sh | bash
```

No environment variables are required: the server defaults to
`~/.mnemos/data` (store) and `~/.mnemos/vault` (Obsidian mirror) and creates
both on first run. See [Tuning](#tuning) for custom locations.

---

## Cursor

Config file: `~/.cursor/mcp.json`. Paste this one line inside the `mcpServers`
object (create the file if it is your first server):

```json
"mnemos": { "type": "stdio", "command": "mnemos", "args": ["mcp-server"] }
```

Fresh setup — create the whole file in one shell line (overwrites an
existing `mcp.json`; otherwise paste the line above into its `mcpServers`):

```bash
echo '{"mcpServers":{"mnemos":{"type":"stdio","command":"mnemos","args":["mcp-server"]}}}' > ~/.cursor/mcp.json
```

Then restart Cursor (or reload the window). The `mnemos_*` tools appear in the
tools list.

## Claude Code

One shell line (registers at user scope — available in every project):

```bash
claude mcp add --scope user mnemos -- mnemos mcp-server
```

Equivalent manual config — `~/.claude.json`, top-level `mcpServers`:

```json
{
  "mcpServers": {
    "mnemos": { "type": "stdio", "command": "mnemos", "args": ["mcp-server"] }
  }
}
```

Verify with `claude mcp list`. Restart running sessions to pick the server up.

## Codex

Config file: `~/.codex/config.toml`. Paste this block (note the underscore
key — `mcp_servers`, not `mcp.servers`):

```toml
[mcp_servers.mnemos]
command = "mnemos"
args = ["mcp-server"]
```

One shell line for a fresh setup:

```bash
mkdir -p ~/.codex && printf '\n[mcp_servers.mnemos]\ncommand = "mnemos"\nargs = ["mcp-server"]\n' >> ~/.codex/config.toml
```

## Windsurf

Config file: `~/.codeium/windsurf/mcp_config.json` (reachable from the Cascade
toolbar: hammer icon → Configure). Paste inside `mcpServers`:

```json
"mnemos": { "type": "stdio", "command": "mnemos", "args": ["mcp-server"] }
```

Fresh setup — create the whole file in one shell line (overwrites an
existing `mcp_config.json`; otherwise paste the line above into `mcpServers`):

```bash
mkdir -p ~/.codeium/windsurf && echo '{"mcpServers":{"mnemos":{"type":"stdio","command":"mnemos","args":["mcp-server"]}}}' > ~/.codeium/windsurf/mcp_config.json
```

## VS Code Copilot (reference)

The scripted path — merges into user- or workspace-scope `mcp.json` safely:

```bash
curl -fsSL https://raw.githubusercontent.com/Korrnals/mnemos/main/scripts/mcp-setup.sh | bash
```

---

## Tuning

Optional environment variables on the server entry (defaults shown). The
names are the canonical `pydantic-settings` form (`MNEMOS_` prefix +
`MNEMOS` section + `__` + field — shorter variants like `MNEMOS_DATA_DIR`
are silently ignored):

| Variable | Default | Purpose |
|---|---|---|
| `MNEMOS_MNEMOS__DATA_DIR` | `~/.mnemos/data` | SQLite store location |
| `MNEMOS_MNEMOS__VAULT_PATH` | `~/.mnemos/vault` | Obsidian vault mirror |

Example — Claude Code with an explicit store path (expanded by your shell):

```bash
claude mcp add --scope user mnemos \
  --env MNEMOS_MNEMOS__DATA_DIR="$HOME/.mnemos/data" \
  --env MNEMOS_MNEMOS__VAULT_PATH="$HOME/.mnemos/vault" \
  -- mnemos mcp-server
```

No secrets belong in these entries — Mnemos on loopback needs no API key.

## Verify the connection

Probe the wire directly (harness-independent — talks stdio):

```bash
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"0.0.0"}}}\n' | mnemos mcp-server
```

A JSON-RPC reply with `"serverInfo":{"name":"mnemos"...}` means the server
answers. Then ask your agent: *“use mnemos_add to save a memory”* — a valid
roundtrip needs the [tag contract](../docs/en/user/tag-contract.md):
one `project:<slug>`, one `agent:<slug>`, at least one `mnemos:<subtype>`.

For any harness not listed here, copy the
[adapter template](adapter-template.md) (Connect / Expose / Configure +
acceptance checklist).
