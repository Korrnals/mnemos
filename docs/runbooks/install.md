# Runbook: Install Mnemos

## Prerequisites

- Python 3.12+
- `uv` (recommended) or `pip`
- Optional: `ollama` for local embeddings

## Quick install

```bash
# Clone
git clone <mnemos-repo> mnemos
cd mnemos

# Create venv and install
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Verify
mnemos --help
pytest tests/ -q
```

## Configuration

Default config lives at `~/.mnemos/config.yaml`. Minimal:

```yaml
mnemos:
  data_dir: ~/.mnemos
  vault_path: ~/mnemos-vault
  strict_tag_contract: true
embedding:
  provider: chromadb  # or onnx, ollama
```

## Start MCP server

Add to VS Code `settings.json`:

```json
{
  "mcpServers": {
    "mnemos": {
      "command": "mnemos",
      "args": ["mcp-server"]
    }
  }
}
```

## Start HTTP API

```bash
mnemos serve  # uvicorn on 127.0.0.1:8787
```

## Verify

```bash
mnemos add "Hello Mnemos" --tags "project:test,agent:manual,gcw:learning"
mnemos search "Hello"
mnemos recall --agent manual --project test
```
