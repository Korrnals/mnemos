# Runbook: Установка Mnemos

**🌐 Language / Язык:** [English](../../../en/admin/runbooks/install.md) · Русский

## Предварительные требования

- Python 3.11+ (wheel — чистый Python плюс встроенная ONNX-модель, этап сборки не нужен)
- `pip` (или `uv` / `pipx` для изолированных установок)
- Опционально: `ollama` для внешнего LLM-обогащения (для хранения и поиска не нужен никогда)

## Быстрая установка (PyPI)

```bash
pip install "mnemos-memory-server[mcp]"
```

- Экстра `mcp` несёт MCP SDK — требуется для `mnemos mcp-server`.
- Модель эмбеддингов (`mnema-embed-v1`) встроена: без скачиваний, работает офлайн.

Изолированный вариант (кладёт CLI `mnemos` в `PATH`, проектные окружения не затрагиваются):

```bash
uv tool install "mnemos-memory-server[mcp]"
# или
pipx install "mnemos-memory-server[mcp]"
```

Скриптовый вариант (venv в `~/.mnemos/venv` + лаунчер в `~/.local/bin` +
опциональная проводка VS Code):

```bash
curl -fsSL https://raw.githubusercontent.com/Korrnals/mnemos/main/scripts/install.sh | bash
```

> ⚠️ Имя пакета на PyPI — `mnemos-memory-server`: `pip install mnemos` устанавливает
> не связанный проект.

## Конфигурация

Конфиг по умолчанию — `~/.mnemos/config.yaml` (опционально — значений по умолчанию
достаточно). Минимальный вариант:

```yaml
mnemos:
  data_dir: ~/.mnemos/data
  vault_path: ~/.mnemos/vault
  strict_tag_contract: true
embedding:
  provider: nano  # mnema-embed-v1 — встроенная локальная модель, работает офлайн; или onnx, ollama
```

Хранилище: `~/.mnemos/data/mnemos.db` (SQLite, WAL). Зеркало vault:
`~/.mnemos/vault/` (Obsidian-совместимый markdown).

## Запуск MCP-сервера

Добавьте в VS Code **User** или **Workspace** `mcp.json`:

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

Пресеты по харнесам (Claude Code, Cursor, OpenCode, Codex, Windsurf, ZCode, pi,
Hermes): [`integrations/mcp-presets.md`](../../../../integrations/mcp-presets.md).
Поведенческий пакет (инструкции / скиллы / промпты): `mnemos integration setup`.

## Запуск HTTP API

```bash
mnemos serve  # uvicorn на 127.0.0.1:8787
```

## Контейнер

Полное контейнерное развёртывание (compose, Kubernetes, systemd quadlet) — см.
[ранбук container-deployment.md](container-deployment.md).

Быстрый запуск одиночного контейнера из выпущенного образа:

```bash
podman run -d -v mnemos-data:/data -v mnemos-vault:/vault -p 8787:8787 \
  --env MNEMOS_API__TOTP_MASTER_KEY=<your-key> ghcr.io/korrnals/mnemos:4.0.0
```

Или через compose из корня репозитория:

```bash
podman-compose up -d
```

## Обновление

```bash
pip install --upgrade "mnemos-memory-server[mcp]"
```

Схема хранилища мигрирует автоматически при первом запуске новой версии.
Делайте бэкап `~/.mnemos/data/` перед мажорными обновлениями — см.
[backup-restore.md](backup-restore.md).

## Проверка

```bash
mnemos add "Hello Mnemos" --tags "project:test,agent:manual,mnemos:learning"
mnemos search "Hello"
mnemos recall --agent manual --project test
```
