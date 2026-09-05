# Runbook: Install Mnemos

**🌐 Language / Язык:** English · [Русский](../../../ru/admin/runbooks/install.md)

## Prerequisites

- Python 3.11+ (the wheel is pure Python plus the bundled ONNX model — no build step)
- `pip` (or `uv` / `pipx` for isolated installs)
- Optional: `ollama` for external LLM enrichment (never needed for storage or search)

## Quick install (PyPI)

```bash
pip install "mnemos-memory-server[mcp]"
```

- The `mcp` extra ships the MCP SDK — required for `mnemos mcp-server`.
- The embedding model (`mnema-embed-v1`) is bundled: no downloads, works offline.

Isolated variant (installs the `mnemos` CLI on `PATH`, project environments untouched):

```bash
uv tool install "mnemos-memory-server[mcp]"
# or
pipx install "mnemos-memory-server[mcp]"
```

Scripted variant (venv at `~/.mnemos/venv` + launcher in `~/.local/bin` + optional VS Code wiring):

```bash
curl -fsSL https://raw.githubusercontent.com/Korrnals/mnemos/main/scripts/install.sh | bash
```

> ⚠️ The PyPI name is `mnemos-memory-server` — `pip install mnemos` installs an unrelated project.

## Configuration

Default config lives at `~/.mnemos/config.yaml` (optional — the defaults are fine). Minimal:

```yaml
mnemos:
  data_dir: ~/.mnemos/data
  vault_path: ~/.mnemos/vault
  strict_tag_contract: true
embedding:
  provider: nano  # mnema-embed-v1 — bundled local model, works offline; or onnx, ollama
```

Store: `~/.mnemos/data/mnemos.db` (SQLite, WAL). Vault mirror: `~/.mnemos/vault/` (Obsidian-compatible markdown).

## Start MCP server

Add to your VS Code **User** or **Workspace** `mcp.json`:

```jsonc
{
  "servers": {
    "mnemos": {
      "type": "stdio",
      "command": "mnemos",
      "args": ["mcp-server"]
    }
  }
}
```

Per-harness presets (Claude Code, Cursor, OpenCode, Codex, Windsurf, ZCode, pi, Hermes):
[`integrations/mcp-presets.md`](../../../../integrations/mcp-presets.md). Behavioral pack (instructions
/ skills / prompts): `mnemos integration setup`.

## Start HTTP API

```bash
mnemos serve  # uvicorn on 127.0.0.1:8787
```

## Container

For full container deployment (compose, Kubernetes, systemd quadlet), see
[container-deployment.md](container-deployment.md).

Quick single-container start using the released image:

```bash
podman run -d -v mnemos-data:/data -v mnemos-vault:/vault -p 8787:8787 \
  --env MNEMOS_API__TOTP_MASTER_KEY=<your-key> ghcr.io/korrnals/mnemos:4.0.0
```

Or with compose from the repo root:

```bash
podman-compose up -d
```

## Upgrade

```bash
pip install --upgrade "mnemos-memory-server[mcp]"
```

The store schema is migrated automatically on first start of the new version. Back up
`~/.mnemos/data/` before major upgrades — see [backup-restore.md](backup-restore.md).

## Verify

```bash
mnemos add "Hello Mnemos" --tags "project:test,agent:manual,mnemos:learning"
mnemos search "Hello"
mnemos recall --agent manual --project test
```
