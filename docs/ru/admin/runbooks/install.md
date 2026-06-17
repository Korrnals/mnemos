# Runbook: Установка Mnemos

**🌐 Language / Язык:** [English](../../../en/admin/runbooks/install.md) · Русский

## Предварительные требования

- Python 3.12+
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
  data_dir: ~/.mnemos
  vault_path: ~/mnemos-vault
  strict_tag_contract: true
embedding:
  provider: chromadb  # или onnx, ollama
```

## Запуск MCP-сервера

Добавить в `settings.json` VS Code:

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

## Запуск HTTP API

```bash
mnemos serve  # uvicorn на 127.0.0.1:8787
```

## Проверка

```bash
mnemos add "Hello Mnemos" --tags "project:test,agent:manual,gcw:learning"
mnemos search "Hello"
mnemos recall --agent manual --project test
```
