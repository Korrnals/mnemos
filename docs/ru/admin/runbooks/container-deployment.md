# Контейнерное развёртывание

**🌐 Language / Язык:** [English](../../../en/admin/runbooks/container-deployment.md) · Русский

> Runbook уровня администратора для сборки, залития и запуска Mnemos в контейнере.
> Охватывает пять путей развёртывания: хелпер `deploy.sh`, `podman-compose`, сырой `podman run`,
> Kubernetes (`podman kube play`) и systemd через quadlet.

---

## Обзор

Mnemos поставляется с `Containerfile` (совместим с OCI, podman/buildah) и готовым `compose.yaml`.
Доступны пять путей развёртывания — выберите подходящий для вашей среды:

| Путь | Инструмент | Когда использовать |
|------|-----------|-------------------|
| `./scripts/deploy.sh` | podman + podman-compose | Разработка и автоматизация — оборачивает все остальные варианты |
| `podman-compose up` | podman-compose | Рекомендуется для production на одном хосте |
| `podman run` | podman | Минимальные зависимости; использование готового образа из ghcr.io |
| `podman kube play` | podman | Kubernetes-подобный pod на одном хосте |
| systemd quadlet | podman + systemd | Постоянный user-сервис с автоматическим перезапуском |

Контейнер открывает **порт 8787** и использует два named volume: `mnemos-data` (SQLite + векторный
индекс) и `mnemos-vault` (Obsidian markdown mirror).

---

## Предварительные требования

- **podman** ≥ 4.0 — rootless-режим полностью поддерживается и рекомендуется
- **podman-compose** — необходим для пути через compose (`pip install podman-compose` или пакет дистрибутива)
- **buildah** — альтернатива `podman build`; опционально
- Python и `git` на хосте **не требуются** — всё запускается внутри контейнера

---

## Сборка

Собрать локальный образ с версией из исходников:

```bash
podman build -t localhost/mnemos:2.1.0 -f Containerfile .
```

`Containerfile` использует `python:3.12-slim` в качестве базового образа, устанавливает `.[mcp]`,
копирует `config.container.yaml` как `/app/config.yaml` и задаёт `CMD ["mnemos", "serve"]` на
порту 8787.

Shortcut через Makefile (собирает `localhost/mnemos:latest`):

```bash
make build-image
```

Хелпер делает то же самое:

```bash
./scripts/deploy.sh build
```

> **CI**: `.github/workflows/release.yml` автоматически собирает и тегирует образ при каждом
> push-е тега `v*.*.*`. Ручная сборка нужна только для локального тестирования или
> нестандартных деплоев.

---

## Залитие в ghcr.io

> Пропустите этот раздел, если вы используете готовый образ из `ghcr.io/korrnals/mnemos`.

**Ручное залитие** (требует PAT с правом `write:packages`):

```bash
podman login ghcr.io
podman tag localhost/mnemos:2.1.0 ghcr.io/korrnals/mnemos:2.1.0
podman push ghcr.io/korrnals/mnemos:2.1.0
podman push ghcr.io/korrnals/mnemos:latest
```

Shortcut через Makefile:

```bash
make push-image
```

> **CI**: `release.yml` автоматически пушит версионный тег и `:latest` в `ghcr.io/korrnals/mnemos`
> с помощью `GITHUB_TOKEN`. Ручное залитие в стандартном цикле релиза не требуется.

---

## Запуск — compose (рекомендуется)

Путь через compose использует `compose.yaml` и монтирует `./config.container.yaml` из корня
репозитория как `/app/config.yaml` внутри контейнера (read-only).

### Запуск

```bash
podman-compose up -d
```

При успехе хелпер выводит:

```text
Mnemos API:   http://localhost:8787
Swagger UI:   http://localhost:8787/docs
```

### Логи

```bash
podman-compose logs -f mnemos
```

### Остановка

```bash
podman-compose down
```

### Обязательная переменная окружения

`config.container.yaml` привязывается к `0.0.0.0`, что **требует** включённой аутентификации.
Передавайте TOTP-мастер-ключ в runtime — никогда не сохраняйте его в файле конфигурации:

```bash
MNEMOS_API__TOTP_MASTER_KEY=<your-key> podman-compose up -d
```

Или добавьте его в файл `.env`, указанный в `.gitignore`:

```bash
echo 'MNEMOS_API__TOTP_MASTER_KEY=<your-key>' >> .env
podman-compose up -d
```

### Ollama sidecar (опциональные embeddings)

Запуск Mnemos совместно с локальным Ollama через встроенный profile:

```bash
podman-compose --profile ollama up -d
```

Скачать модель эмбеддингов в sidecar:

```bash
podman exec mnemos-ollama ollama pull nomic-embed-text
```

Чтобы активировать Ollama как провайдер эмбеддингов, обновите `config.container.yaml`:

```yaml
embedding:
  provider: ollama
  model: nomic-embed-text
  ollama_url: http://ollama:11434
```

---

## Запуск — одиночный контейнер

Скачать готовый образ и сразу запустить:

```bash
podman pull ghcr.io/korrnals/mnemos:2.1.0
podman run -d -v mnemos-data:/data -v mnemos-vault:/vault -p 8787:8787 \
  --env MNEMOS_API__TOTP_MASTER_KEY=<your-key> ghcr.io/korrnals/mnemos:2.1.0
```

В образ встроен `config.container.yaml` как `/app/config.yaml` — отдельное монтирование
конфига не требуется, если только вам не нужно переопределить настройки.

---

## Запуск — Kubernetes

Mnemos поставляется с Kubernetes-подобным манифестом pod'а (`deploy/kube/mnemos-pod.yaml`)
для `podman kube play`. Манифест использует `PersistentVolumeClaims`, поэтому named volumes
необходимо создать заранее.

### Запуск

```bash
podman volume create mnemos-data
podman volume create mnemos-vault
podman kube play deploy/kube/mnemos-pod.yaml
```

Shortcut (создаёт volumes автоматически перед запуском манифеста):

```bash
./scripts/deploy.sh kube-up
```

### Остановка

```bash
podman kube down deploy/kube/mnemos-pod.yaml
```

Shortcut:

```bash
./scripts/deploy.sh kube-down
```

---

## Запуск — systemd (quadlet)

Путь через quadlet устанавливает systemd **user**-юнит и управляет контейнером как постоянным
сервисом. Юнит ссылается на `localhost/mnemos:latest`, поэтому сначала соберите образ локально
(см. [Сборка](#сборка)).

### Задать TOTP-ключ

Отредактируйте `deploy/quadlet/mnemos.container` и добавьте ключ перед установкой:

```ini
[Container]
# ... существующие строки ...
Environment=MNEMOS_API__TOTP_MASTER_KEY=<your-key>
```

### Установка юнита

```bash
./scripts/deploy.sh quadlet
```

Копирует `deploy/quadlet/mnemos.container` в `~/.config/containers/systemd/` и выполняет
`systemctl --user daemon-reload`.

### Запуск и автозапуск

```bash
systemctl --user start mnemos
systemctl --user enable mnemos   # автозапуск при входе в систему
```

### Проверка статуса

```bash
systemctl --user status mnemos
```

---

## Конфигурация

Mnemos использует `config.container.yaml` в качестве конфига контейнера. Файл:

- Встроен в образ при сборке как `/app/config.yaml`
- Перекрывается в compose-пути монтированием `./config.container.yaml:/app/config.yaml:ro`

Ключевые настройки:

| Параметр | Значение | Примечания |
|---------|---------|-----------|
| `mnemos.data_dir` | `/data` | Mapped to named volume `mnemos-data` |
| `mnemos.vault_path` | `/vault` | Mapped to named volume `mnemos-vault` |
| `api.host` | `0.0.0.0` | Привязка ко всем интерфейсам — **требует auth** |
| `api.port` | `8787` | Внутренний порт контейнера; маппинг задаётся в compose/run |
| `api.auth_enabled` | `true` | Обязательно `true` при `host: 0.0.0.0` |
| `api.totp_enabled` | `true` | Требует TOTP 2FA; ключ через `MNEMOS_API__TOTP_MASTER_KEY` |
| `api.behind_tls_proxy` | `true` | TLS завершается выше по стеку (Caddy, nginx и т.п.) |
| `embedding.provider` | `chromadb` | Встроенный ONNX; GPU не требуется |

### Требования безопасности

Привязка к `0.0.0.0` **требует** одновременно `auth_enabled: true` и `totp_enabled: true`.
TOTP-мастер-ключ должен передаваться через `MNEMOS_API__TOTP_MASTER_KEY` — он никогда не должен
присутствовать в файле конфигурации или в любом коммитируемом файле.

Размещайте Mnemos за TLS-терминирующим реверс-прокси (Caddy, nginx и т.п.).
Задайте `trusted_proxies` с CIDR-диапазоном вашего прокси, чтобы заголовки `X-Forwarded-For`
доверялись корректно.

Полную модель угроз и детали конфигурации аутентификации см. в [../security.md](../security.md).

### Провайдер эмбеддингов

- **По умолчанию**: `chromadb` со встроенными ONNX-эмбеддингами (без torch, без GPU)
- **Ollama sidecar**: задайте `embedding.provider: ollama` и `embedding.ollama_url: http://ollama:11434`
  (см. [Ollama sidecar](#ollama-sidecar-опциональные-embeddings) выше)

---

## Здоровье и операции

### Healthcheck контейнера

Healthcheck в compose запускает `mnemos stats` каждые 30 секунд с таймаутом 5 секунд.
Проверить текущее состояние:

```bash
podman inspect --format '{{.State.Health.Status}}' mnemos
```

### Обзор статуса

```bash
./scripts/deploy.sh status
```

Выводит запущенные контейнеры (имя, статус, порты) и named volumes.

### Доступ через shell

```bash
./scripts/deploy.sh shell
# эквивалентно: podman exec -it mnemos /bin/bash
```

### Запуск CLI внутри контейнера

```bash
./scripts/deploy.sh cli search "hello"
# эквивалентно: podman exec mnemos mnemos search "hello"
```

---

## См. также

- [install.md](install.md) — установка на bare-metal / в virtualenv
- [../security.md](../security.md) — модель угроз, аутентификация, SSRF-защита
- [../../user/getting-started.md](../../user/getting-started.md) — руководство по первому запуску

---

_Исходные файлы: `Containerfile`, `compose.yaml`, `config.container.yaml`, `scripts/deploy.sh`,
`deploy/quadlet/mnemos.container`, `deploy/kube/mnemos-pod.yaml`_
