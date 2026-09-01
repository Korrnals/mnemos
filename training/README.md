# training/ — инфраструктура обучения нано-модели (NM-1a/b)

Каталог training-инфраструктуры трека нано-модели (ADR-0021,
`docs/project/nano-model-plan.md` §3). Этап **NM-1a** (этот): датасет-преп,
скрипт дистилляции, экспорт int8 ONNX, eval-джига, one-command пайплайн.
Этап **NM-1b** (прогон владельцем): фактическая дистилляция 45–60M-студента
поверх учителя, калибровка int8, артефакт ≤60 МБ.

Анти-скоуп ADR-0021: **обучение отделено от рантайма**. Ничего из
`training/` не попадает в wheel/sdist (исключение в `pyproject.toml`), не
импортируется из `src/mnemos/`, не запускается в CI (CI проверяет только
хеш артефакта). Зависимости обучения объявлены в
`training/requirements.txt`, НЕ в `pyproject.toml`.

Язык: русский, по стилю `docs/project/dev-plan.md`. Отчёты eval — в
`benchmarks/reports/` (gitignored).

## Карта файлов

| Путь | Назначение |
| --- | --- |
| `dataset/prepare_dataset.py` | Сбор memory-shaped RU+EN корпуса (фикстуры репо + синтетика + опц. локальный стор), дедуп, лимит 256 токенов, RU-квота ≥40 %, train/val 95/5 (seed=42) |
| `dataset/synthetic_templates.py` | Программные RU+EN шаблоны (заметки/чат-выдержки/код-заголовки), 50+ шаблонов × вариации |
| `distill.py` | Дистилляция студента (KD-loss: MSE на cos-similarity к учителю, temperature-скейл), чекпоинты per-epoch, детерминизм |
| `export_onnx.py` | Экспорт студента в ONNX (opset пин, static shapes 1×256), int8 static PTQ, `manifest.json` с fingerprints |
| `eval_distilled.py` | Eval-джига: cos-sim студент/учитель на val; retrieval-proxy recall@5 против BM25-эталона на judged-корпусе; сводка JSON+markdown |
| `run_pilot.sh` | One-command пайплайн NM-1b: prepare → distill → export → eval |
| `requirements.txt` | Зависимости обучения (torch и пр.) — вне рантайма |
| `logs/` | Логи прогонов (gitignored) |

## Требования (Silverblue-специфика)

ОС хоста — Fedora Silverblue (неизменяемый корень): **всё выполняется в
контейнере**, ничего не ставим на хост. Контейнер toolbox/distrobox с
Python 3.12 и torch+cpu:

```bash
# одноразово: контейнер с training-окружением
toolbox create mnemos-training -c fedora:40   # или distrobox create
toolbox enter mnemos-training
cd /path/to/mnemos
python3 -m pip install -r training/requirements.txt
```

Модель-учитель скачивается при прогоне NM-1b у владельца (см. ниже) —
**никаких сетевых загрузок моделей в CI или сессии подготовки**.

## Запуск

Все команды — из корня репо внутри контейнера.

### 1. Датасет (NM-1a; без сети, без torch)

```bash
# полный корпус до 100k пар (по умолчанию)
python3 training/dataset/prepare_dataset.py \
  --out-dir training/data --seed 42

# с приватной выгрузкой локального стора владельца (данные не выходят с машины)
python3 training/dataset/prepare_dataset.py --from-mnemos-dir ~/.local/share/mnemos

# кап по числу пар (смоук)
python3 training/dataset/prepare_dataset.py --max-pairs 500 --out-dir /tmp/nm1a-smoke
```

Выход: `train.jsonl` / `val.jsonl` (95/5, seed=42 детерминизм), строки
`{"text", "lang", "source"}`, fingerprint печатается в stdout.

### 2. Дистилляция (NM-1b; torch; CPU/iGPU через IPEX, иначе CPU-потоки)

```bash
python3 training/distill.py \
  --teacher sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
  --student-init sentence-transformers/paraphrase-multilingual-MiniLM-L6-v2 \
  --pairs training/data/train.jsonl --val training/data/val.jsonl \
  --epochs 3 --batch-size 32 --threads 4 --out-dir training/runs/nm1b
```

- чекпоинты каждую эпоху: `training/runs/nm1b/epoch<N>/`;
- лог метрик per-epoch: avg KD-loss + cos-sim студента к учителю на val;
- детерминизм: seed фикс (все источники случайности);
- `--max-pairs 100 --epochs 1` — dry-режим смоука (без GPU, медленно).

### 3. Экспорт ONNX + int8 PTQ (после дистилляции)

```bash
python3 training/export_onnx.py \
  --run-dir training/runs/nm1b --out-dir training/runs/nm1b/onnx \
  --calib-samples 200
```

Выход: `model.onnx` (static shapes 1×256, opset пин в константе), +
`tokenizer.json`, + `manifest.json` (base_teacher, student_params,
dataset_fingerprint sha256 jsonl, weights_sha256 пост-экспорт, opset,
created, license).

### 4. Eval-джига

```bash
python3 training/eval_distilled.py \
  --run-dir training/runs/nm1b --onnx-dir training/runs/nm1b/onnx \
  --report-dir benchmarks/reports
```

- (а) cos-sim студента к учителю на val: среднее/медиана/распределение;
- (б) retrieval-proxy recall@5 на judged-корпусе (`benchmarks/corpus`):
  студент против BM25-лексического эталона; сравнительная таблица
  студент/учитель/chromadb-MiniLM.
- Выход: JSON + markdown-сводка в `benchmarks/reports/`.

### 5. Пайплайн пилота (one command)

```bash
THREADS=4 MAX_PAIRS=100000 EPOCHS=3 bash training/run_pilot.sh
```

env-ручки: `THREADS` (потоки torch, default: половина ядер),
`MAX_PAIRS` (кап датасета, default 100000), `EPOCHS` (default 3).
Логи — `training/logs/pilot-<ts>.log`; таймауты в шапке скрипта.

### Тесты

```bash
/usr/bin/python3.12 -m pytest tests/test_training_dataset.py -q -p no:cacheprovider
```

Smoke-тесты датасет-препа и manifest-схемы без torch: детерминизм seed,
RU-квота, дедуп, лимит 256 токенов. Тяжёлые импорты (torch/transformers)
мокаются; если torch в окружении нет — тесты скипаются с причиной.

## Политика параллельной работы (ноутбук)

- Прогон — **в фоне** (`nohup`/`tmux`/`toolbox run -t … &`), не в
  интерактивной сессии агента: дистилляция занимает часы.
- Троттлинг потоков: `--threads` ≤ половина физических ядер; в пайплайне —
  env `THREADS`. Меньше потоков = холоднее ноутбук = стабильнее.
- **Питание/крышка**: ноутбук подключить к питанию, крышку НЕ закрывать
  (или `loginctl inhibit-sleep` / настройка suspend-при-закрытии-крышки
  для сессии прогона). Уход в suspend убивает прогон — чекпоинты
  per-epoch смягчают потерю, но эпоха перезапустится заново.

## Политика приватности и лицензий

- `--from-mnemos-dir` читает только **локальный** стор владельца; данные
  не покидают машину (ни сети, ни выгрузок — прогон NM-1b автономен).
- Учитель — только Apache-2.0/MIT-лицензии. Кандидаты:

| Модель (HF) | Лицензия | dim | Комментарий |
| --- | --- | --- | --- |
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Apache-2.0 | 384 | дефолт NM-1a/b: мультиязычный, RU+EN, парафразная близость |
| `sentence-transformers/LaBSE` | Apache-2.0 | 768 | сильнее на переводных парах, тяжелее (471M) |
| `sentence-transformers/distiluse-base-multilingual-cased` | Apache-2.0 | 768 | distil-класс, 12 языков (RU входит) |

Выбор — параметр `--teacher` (у `distill.py` и `export_onnx.py` через
manifest `base_teacher`).

## Статус NM-1a / NM-1b

- NM-1a (скелет): этот каталог; детерминированность скриптов,
  smoke-тесты, prepare без сети — готово.
- NM-1b (прогон владельцем): скачивание учителя, полный корпус 100k,
  фактическая дистилляция, int8-калибровка, eval-отчёт с порогом
  provisional (cos-sim ≥0.95 к учителю; retrieval-proxy не ниже
  учитель − 2 %) — отложено на прогон у владельца (GPU-хост по §4 плана).