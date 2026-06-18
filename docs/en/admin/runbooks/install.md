# Runbook: Install Mnemos

**🌐 Language / Язык:** English · [Русский](../../../ru/admin/runbooks/install.md)

## Prerequisites

- Python 3.11+
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
  --env MNEMOS_API__TOTP_MASTER_KEY=<your-key> ghcr.io/korrnals/mnemos:1.1.1
```

Or with compose from the repo root:

```bash
podman-compose up -d
```

## Verify

```bash
mnemos add "Hello Mnemos" --tags "project:test,agent:manual,gcw:learning"
mnemos search "Hello"
mnemos recall --agent manual --project test
```
