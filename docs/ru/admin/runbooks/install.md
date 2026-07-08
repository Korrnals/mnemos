# Runbook: Установка Mnemos

**🌐 Language / Язык:** [English](../../../en/admin/runbooks/install.md) · Русский

## Предварительные требования

- Python 3.11+
- `uv` (рекомендуется) или `pip`
- Опционально: `ollama` для локальных embeddings

## Быстрая установка

```bash
# Клонирование
git clone <mnemos-repo> mnemos
cd mnemos

# Создание venv и установка
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Проверка
mnemos --help
pytest tests/ -q
```

## Конфигурация

Конфиг по умолчанию хранится в `~/.mnemos/config.yaml`. Минимальный вариант:

```yaml
mnemos:
  data_dir: ~/.mnemos/data
  vault_path: ~/.mnemos/vault
  strict_tag_contract: true
embedding:
  provider: chromadb  # или onnx, ollama
```

## Запуск MCP-сервера

Добавить в VS Code **User** или **Workspace** `mcp.json`:

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

## Запуск HTTP API

```bash
mnemos serve  # uvicorn на 127.0.0.1:8787
```

## Контейнер

Полное контейнерное развёртывание (compose, Kubernetes, systemd quadlet) — см.
[ранбук container-deployment.md](container-deployment.md).

Быстрый запуск одиночного контейнера из готового образа:

```bash
podman run -d -v mnemos-data:/data -v mnemos-vault:/vault -p 8787:8787 \
  --env MNEMOS_API__TOTP_MASTER_KEY=<your-key> ghcr.io/korrnals/mnemos:2.1.0
```

Или через compose из корня репозитория:

```bash
podman-compose up -d
```

## Проверка

```bash
mnemos add "Hello Mnemos" --tags "project:test,agent:manual,mnemos:learning"
mnemos search "Hello"
mnemos recall --agent manual --project test
```
