# Mnemos — architecture

> Companion to [PLAN.md](PLAN.md). PLAN is the *how* (phases, tasks, ordering). ARCHITECTURE is the *what* (components, interfaces, data, decisions).

## 1. System overview

Mnemos is a single-tenant memory/knowledge service for AI agents (primarily Copilot agents in VS Code, via MCP). It is forked from `ai-brain` and retains its core stack:

- **Runtime**: Python 3.11+, FastAPI HTTP API, Typer CLI, MCP server (stdio + optional SSE).
- **Storage**: SQLite (FTS5) for raw + processing + processed, ChromaDB (vector) only for `published` knowledge units, Obsidian-compatible vault on disk for human-readable mirror.
- **Embeddings**: local ONNX (MiniLM-class) — privacy + offline.
- **Packaging**: rootless `podman` container; systemd quadlet units; user-level install option.

### Conceptual layers

```mermaid
flowchart TB
    subgraph CLIENTS["Clients"]
        C1(["VS Code · Copilot\n(stdio MCP)"])
        C2(["CLI — mnemos …"])
        C3(["HTTP API client"])
    end

    subgraph IFACE["Interface Layer"]
        MCP["mcp_server.py"]
        FAPI["api/main.py\nFastAPI"]
        TYPER["cli/main.py\nTyper"]
    end

    MGR(["MemoryManager\nmanager.py"])

    subgraph PROC["Processing Subsystems"]
        CF["Context Filter\nfilter/"]
        PP["Knowledge Pipeline\npipeline/"]
        RE["Recall Engine\nrecall/"]
        PE["Policy Engine\npolicy/"]
    end

    subgraph BG["Background Services"]
        WA["Watchers\nwatchers/"]
        AC["Auto-collect\nauto_collect.py"]
    end

    subgraph STORE["Storage Layer"]
        SQ[("SQLite\nFTS5 · traces · projects")]
        VS[("Vector Store\nnumpy + SQLite")]
        VLT[("Obsidian Vault\nmarkdown mirror")]
    end

    C1 -->|"stdio"| MCP
    C2 --> TYPER
    C3 --> FAPI

    MCP --> MGR
    TYPER --> MGR
    FAPI --> MGR

    MGR --> CF
    MGR --> PP
    MGR --> RE
    MGR --> SQ
    MGR --> VS
    MGR --> VLT

    CF -.->|"raw + clean"| SQ
    PP -->|"status transitions"| SQ
    PP -->|"published upsert"| VS
    RE -->|"FTS5 MATCH"| SQ
    RE -->|"cosine search"| VS

    PE -->|"schedule / trigger"| MGR
    WA -->|"file events"| MGR
    AC -.->|"checkpoint reminder"| MCP
```

## 2. Core data model

### `Memory` (single unified table, status-driven)

| field | type | notes |
|---|---|---|
| `id` | uuid | primary key |
| `content` | text | markdown body |
| `tags` | array<string> | validated by `TagContract` |
| `project` | string | denormalised from `project:*` tag |
| `agent` | string | denormalised from `agent:*` tag |
| `status` | enum | `raw \| processing \| processed \| published` |
| `quality_score` | float? | populated by synthesis / quality-gate |
| `confidence` | float? | populated by synthesis |
| `source_coverage` | int? | distinct source URLs / paths in cluster |
| `cluster_id` | string? | set during clustering |
| `derived_from` | array<uuid> | provenance for `processed`/`published` |
| `embedding_id` | string? | ChromaDB id when published |
| `raw_content` | text? | immutable source payload (logs/stdout/html/etc.) |
| `clean_content` | text? | filtered projection used for recall/model input |
| `filter_profile` | string? | `log|terminal|code|docs|web|default` |
| `filter_stats` | json? | token + dedup reduction stats |
| `filter_version` | string? | filter pipeline version used for this record |
| `created_at` | datetime | |
| `updated_at` | datetime | |

### `TagContract`

Required composition for any `mnemos_add`:
- exactly one `project:<slug>` tag
- exactly one `agent:<slug>` tag (or `agent:user` for human-authored)
- ≥1 tag from `gcw:*` namespace (`gcw:session`, `gcw:bug-pattern`, `gcw:learning`, `gcw:decision`, `gcw:rule`, `gcw:open-question`, `gcw:checkpoint`, `gcw:legacy`)
- Optional whitelisted prefixes: `severity:`, `stack:`, `applyTo:`, `source:`

Enforcement: at MCP layer when `strict_tag_contract=true` (default for new installs). Lax mode tags legacy records `gcw:legacy` + `agent:unknown` automatically.

### `Trace`

Per-pipeline-step audit row (M6):
`task_label, project, step, item_id, llm_called, llm_done, cache_hit, fallback_used, latency_ms, tokens_in/out, tokens_per_sec, rationale_summary (≤200 chars, NO chain-of-thought)`.

### Data model diagram

```mermaid
classDiagram
    class Memory {
        +str id
        +str content
        +list~str~ tags
        +str project
        +str agent
        +MemoryStatus status
        +float quality_score
        +float confidence
        +int source_coverage
        +str cluster_id
        +list~str~ derived_from
        +str raw_content
        +str clean_content
        +str filter_profile
        +datetime created_at
        +effective_content() str
        +auto_title() str
    }
    class MemoryStatus {
        <<enumeration>>
        RAW
        PROCESSING
        PROCESSED
        PUBLISHED
        ARCHIVED
    }
    class TagContract {
        +list~str~ tags
        +bool strict
        +str project
        +str agent
        +list~str~ gcw_subtypes
    }
    class Trace {
        +str id
        +str task_label
        +str project
        +str step
        +int latency_ms
        +int tokens_in
        +int tokens_out
        +str rationale_summary
        +datetime created_at
    }
    class Project {
        +str id
        +str name
        +list~str~ paths
        +datetime created_at
    }

    Memory --> MemoryStatus : has status
    Memory ..> TagContract : validated by
    Memory "0..*" --> "1" Project : belongs to
    Trace "0..*" --> "1" Project : logged for
```

## 3. Interfaces

### MCP tools (stable names — the GCW stub plugin already references these)

| tool | purpose |
|---|---|
| `mnemos_add` | write a memory (validated by TagContract) |
| `mnemos_search` | hybrid search (FTS + vector + RRF) over `published` |
| `mnemos_recall_context` | session-init: most recent N for a project |
| `mnemos_agent_recall` | filter by `agent:` (+ optional project / query) — **new in Mnemos** |
| `mnemos_save_context` | checkpoint-style write with auto-tagged `gcw:checkpoint` |
| `mnemos_list_recent` | recency-ordered listing |
| `mnemos_list_tags` | tag directory |
| `mnemos_ingest_url` | URL ingest → raw |
| `mnemos_watch_start/stop/status` | vault watcher control |
| `mnemos_auto_collect_status` | compaction-signal report |
| `mnemos_stats` | health + counters |

### HTTP API

Mirrors MCP tools (`POST /memories`, `GET /recall/...`, `GET /search`, etc.) plus pipeline endpoints `POST /process`, `POST /synthesize`, `POST /publish`, `GET /memories?status=`, `GET /traces`, `GET /metrics`.

### CLI

`mnemos add`, `mnemos search`, `mnemos recall --agent <x>`, `mnemos cluster`, `mnemos synthesize`, `mnemos publish`, `mnemos tags validate`, `mnemos migrate-from-ai-brain`, `mnemos dlq list/retry/discard`, `mnemos watch --include-rules`.

## 4. Knowledge pipeline (M4) — the core architectural addition

```mermaid
flowchart TD
    ADD["mnemos_add / ingest_url"]
    RAW[("status: raw")]

    subgraph CL["Cluster Worker — pipeline/cluster.py"]
        CL1["group by embedding similarity\nassign cluster_id"]
    end

    PROC[("status: processing")]

    subgraph SY["Synthesize Worker — pipeline/synthesize.py"]
        SY1["LLM draft synthesis\nidempotency: hash(cluster_id, prompt_v, model_v)"]
    end

    PCED[("status: processed")]

    subgraph QG["Quality Gates — pipeline/quality_gate.py"]
        QG1{"quality_score\nconfidence\nsource_coverage"}
    end

    PUB[("status: published")]
    VEC[("Vector Index\nVectorStore")]

    subgraph DLQ_B["DLQ — policy/dlq.py"]
        DLQ["Dead-Letter Queue\nfailed synthesis items"]
    end

    ADD --> RAW
    RAW --> CL
    CL --> PROC
    PROC --> SY
    SY --> PCED
    PCED --> QG
    QG1 -->|"all thresholds pass"| PUB
    QG1 -->|"any threshold fails"| DLQ
    PUB --> VEC
    DLQ -->|"retry (exp. backoff)"| SY
    DLQ -->|"max retries reached"| PCED
```

**Key invariant**: only `status="published"` ever lives in the vector index. This is what makes hybrid recall high-signal: noise is filtered upstream by quality gates, not by ranking heuristics.

## 4a. Context Filter (M10) — pre-LLM token-noise reduction

Context Filter sits between interface input and downstream pipeline/recall so the model receives concise, semantically complete context instead of raw noise.

### Invariant

- Filtering never destroys data.
- `raw_content` is always retained for audit/drill-down.
- `clean_content` is the default payload for retrieval and model-facing flows.

### Pipeline

1. **Dedup** (`dedup.py`) — exact + near-duplicate suppression.
2. **Noise strip** (`noise.py`) — ANSI escape removal, progress bars, repeated separators, timestamp prefixes.
3. **Signal extract** (`extract.py`) — keep errors/warnings/exit-status + informative slices for large outputs.
4. **Compress** (`compress.py`) — semantic compression for repetitive blocks.
5. **Token estimate** (`tokens.py`) — preflight token budgeting and reduction accounting.

### Profiles

Configured in `~/.mnemos/filter_profiles.yaml`:

- `log`
- `terminal`
- `code`
- `docs`
- `web`
- `default`

Selection priority: explicit request → `source:` tag hint → content heuristics → `default`.

### API behavior

- `mnemos_add`: optional `filter_profile`, stores both raw and clean forms.
- recall/search tools return `clean_content` by default.
- `include_raw=true` enables drill-down to source payload.

## 5. Policy engine (M5)

Declarative YAML rules (`~/.mnemos/policies.yaml`):
- Auto-publish thresholds (quality + confidence + source-coverage).
- Defer / archive rules based on age, status, cluster size.
- Per-project overrides.

Reliability primitives:
- **Idempotency** — synthesis is keyed on `hash(cluster_id, prompt_version, model_version)`. Repeats return cached result. This is also the v1 stand-in for the deferred Cache Center.
- **DLQ** — failed synthesis lives here; manual `mnemos dlq retry/discard`.
- **Retry** — exponential backoff with jitter; capped attempts.

## 6. Recall & ranking

- **FTS5**: SQLite full-text index over `content` + `tags`.
- **Vector**: ChromaDB on `published` only.
- **Fusion**: Reciprocal Rank Fusion (RRF) of the two result lists.
- **Per-agent recall** (M3): pre-filter by `agent:<slug>` (+ optional `project:<slug>`) before search; index covers `(tag_value, project_value)`.
- **File-context boost** (M8): when a `current_file_path` is provided, rules with matching `applyTo:` glob are pinned to the top.
- **Filtered output default** (M10): recall returns `clean_content` unless `include_raw=true` is explicitly requested.

## 7. Compaction detection (M7)

Auto-collect signals (weighted, configurable in `~/.mnemos/auto_collect.yaml`):
1. **Call counter** (inherited from ai-brain): N calls in T seconds → suggest checkpoint.
2. **Context-size heuristic**: client-reported token estimate > 80 % of model limit.
3. **Summary-marker detection**: regex on the most recent inbound messages for `<conversation-summary>` / `<compacted>`.
4. **Reference-drop heuristic**: agent stops citing earlier identifiers in the last N tool calls.

`mnemos_auto_collect_status` returns the per-signal vector + composite recommendation.

## 8. Path-scoped rules ingest (M8)

File watcher on `.github/instructions/*.instructions.md` in configured repos. On change:
- Parse frontmatter (`applyTo:` glob).
- Create / update a `Memory` with `status=published`, tags `gcw:rule`, `project:<repo>`, `applyTo:<glob>`, `source:path-scoped-rule`.
- On delete → remove memory + vector entry.

This makes path-scoped rules first-class searchable knowledge instead of inert instruction files.

## 9. Security & operational posture

- **Rootless podman** by default. MCP server bound to localhost / unix-socket; HTTP API loopback only unless explicitly bound.
- **Secrets**: provider API keys via env vars (`MNEMOS_ANTHROPIC_API_KEY`, …) read once at startup; never written to logs.
- **URL ingest sanitisation**: strip credentials from URLs before storing.
- **Explainability**: only short `rationale_summary` (≤200 chars), never raw LLM chain-of-thought.
- **Filter safety**: Context Filter never removes source data; raw payload remains retrievable for audit/debug.
- **Quotas**: per-project soft cap on raw count; alert at 90 %.
- **Audit**: `traces` table is append-only.

## 10. Migration & deprecation

- `mnemos migrate-from-ai-brain` (M13): SQLite + vault import; lax tag mode for legacy data; backup first; dry-run flag.
- ai-brain (M14): README header marks it `DEPRECATED`; tag `final-v0.2.x`; main branch frozen.

## 11. Module layout (Python)

> **Note**: Uses `src/` layout (inherited from ai-brain) to keep the Python package off `sys.path` by default and prevent accidental shadowing.

```
pyproject.toml
src/
  mnemos/
    __init__.py
    config.py            # env + YAML config
    models.py            # Memory, TagContract, Trace, Cluster
    manager.py           # MemoryManager — CRUD + search orchestrator
    storage/
      __init__.py
      sqlite_store.py    # SQLite FTS5 + pipeline state
      vector_store.py    # ChromaDB (published-only)
      vault.py           # Obsidian markdown mirror
    llm/
      __init__.py
      base.py            # provider abstraction
      anthropic.py
      openai.py
      azure_openai.py
      ollama.py
      gemini.py
    embeddings/
      __init__.py
      onnx_local.py      # local ONNX MiniLM (privacy + offline)
    pipeline/
      __init__.py
      cluster.py
      synthesize.py
      quality_gate.py
      publish.py
    policy/
      __init__.py
      scheduler.py
      triggers.py
      engine.py          # YAML rule evaluation
      dlq.py
    filter/
      __init__.py
      dedup.py
      noise.py
      extract.py
      compress.py
      tokens.py
    recall/
      __init__.py
      fts.py
      vector.py
      rrf.py
      agent_recall.py
    watchers/
      __init__.py
      vault.py
      path_scoped.py
    traces.py            # explainability layer
    auto_collect.py      # compaction signals
    mcp_server.py
    api/
      __init__.py
      main.py            # FastAPI
      routes/
    cli/
      __init__.py
      main.py            # Typer
      migrate.py
docs/
  tag-contract.md
  pipeline.md
  policies.md
  runbooks/
tests/
  __init__.py
  test_tag_contract.py
  test_agent_recall.py
  test_pipeline.py
  test_policy_engine.py
  test_traces.py
  test_compaction_detection.py
  test_path_scoped_rules.py
  test_migration.py
  test_recall.py
  test_filter.py
  ...
```

### M1 Git bootstrap commands (run once in mnemos/ dir)

```bash
# Step 1: clone ai-brain history into a temp directory
git clone /var/home/abyss/LABs/AI/ai-brain /tmp/mnemos-bootstrap

# Step 2: copy planning docs into temp clone
cp README.md PLAN.md ARCHITECTURE.md /tmp/mnemos-bootstrap/

# Step 3: copy .git from temp clone into mnemos/
cp -r /tmp/mnemos-bootstrap/.git .

# Step 4: rename origin → upstream-ai-brain (read-only reference)
git remote rename origin upstream-ai-brain
git remote set-url --push upstream-ai-brain DISABLED  # prevent accidental push

# Step 5: stage all changes and commit the fork baseline
git add -A
git commit -m "chore(m1): fork from ai-brain; add Mnemos planning documents"

# Step 6: (optional) set a new origin when you have a Mnemos repo
# git remote add origin <your-mnemos-remote-url>
```

## 12. Out of scope for v1 (explicit)

- **Cache Center** (M11) — deferred to v2.
- **New Web UI from scratch** — if ai-brain has one, we extend; if not, Swagger + mkdocs only.
- **Multi-tenant / multi-user auth** — Mnemos is single-tenant by design.
- **Cloud-managed embeddings** — local ONNX only.
- **Cross-machine sync** — out of scope; v2 if demanded.

## 13. Open questions for the implementation session

1. Confirm local ONNX embeddings (recommendation: keep).
2. Final list of LLM providers for synthesis at launch (current set: Anthropic + OpenAI + Azure OpenAI + Ollama + Gemini).
3. mcp.json server-name aliasing policy.
4. Git strategy verification: `git clone` + remote-rename approach OK?

## 14. Component diagrams

### Context Filter pipeline

```mermaid
flowchart TD
    IN["Input Content\n(log / terminal / code / docs / web)"]

    subgraph SEL["Profile Selection"]
        P1["① explicit request"]
        P2["② source: tag hint"]
        P3["③ content heuristics"]
        P4["④ default profile"]
        P1 -.- P2 -.- P3 -.- P4
    end

    subgraph PIPE["5-Stage Filter Pipeline — filter/"]
        D["① dedup.py\nexact + near-dup suppression"]
        N["② noise.py\nANSI · progress bars · separators"]
        E["③ extract.py\nerrors · warnings · exit codes"]
        C["④ compress.py\nsemantic block compression"]
        T["⑤ tokens.py\npreflight budget + reduction stats"]
        D --> N --> E --> C --> T
    end

    RAW[("raw_content\n← immutable audit copy")]
    CLEAN[("clean_content\n← default for recall / models")]
    STATS["filter_stats\n{ profile, tokens_before, tokens_after }"];

    IN --> SEL
    SEL --> PIPE
    IN -.->|"always preserved"| RAW
    T --> CLEAN
    T --> STATS
```

### Storage layer

```mermaid
flowchart TD
    MGR["MemoryManager"]

    subgraph SQL["SQLite — storage/sqlite_store.py"]
        MEM[("memories\n(main table)")]
        FTS[("memories_fts\nFTS5 virtual table")]
        TR[("traces\nappend-only audit")]
        PRJ[("projects")]
        MEM <-.->|"triggers AI / AD / AU"| FTS
    end

    subgraph VST["Vector Store — storage/vector_store.py"]
        EMB[("embeddings table\nvectors.db — numpy float32")]
    end

    subgraph VLT_B["Obsidian Vault — storage/vault.py"]
        MD[("*.md files\nvault/{type}/{title}.md\nYAML frontmatter")]
    end

    MGR -->|"save / get / delete / update"| MEM
    MGR -->|"save_trace"| TR
    MGR -->|"save_project"| PRJ
    MGR -->|"upsert / search (published only)"| EMB
    MGR -->|"memory_to_file / scan / delete_file"| MD
```

### Hybrid recall engine

```mermaid
flowchart TD
    Q["Search Query\n{ query, tags, project, agent, limit }"]
    EMBED["Embeddings\nembeddings/__init__.py\nquery → 384-dim vector"]

    subgraph LEGS["Dual-Leg Retrieval"]
        FTS_L["FTS5 Leg — recall/fts.py\nSQLite FTS5 MATCH + filters"]
        VEC_L["Vector Leg — recall/vector.py\ncosine similarity on published"]
    end

    AGENT{"agent_recall?\n(M3)"}
    AFILT["Pre-filter\nagent: + project:"]
    RRF["RRF Fusion — recall/rrf.py\nrrf_k = 60  ·  alpha blend"]
    OUT["SearchResult[]\n{ memory, score, search_type }"]

    Q --> AGENT
    AGENT -->|"yes"| AFILT
    AFILT --> LEGS
    AGENT -->|"no"| LEGS

    Q --> EMBED
    EMBED --> VEC_L
    Q --> FTS_L
    FTS_L & VEC_L --> RRF --> OUT
```

### MCP tools → MemoryManager

```mermaid
flowchart LR
    subgraph TOOLS["MCP Tools — mcp_server.py"]
        T1["mnemos_add"]
        T2["mnemos_search"]
        T3["mnemos_recall_context"]
        T4["mnemos_agent_recall"]
        T5["mnemos_save_context"]
        T6["mnemos_list_recent"]
        T7["mnemos_list_tags"]
        T8["mnemos_ingest_url"]
        T9["mnemos_watch_*"]
        T10["mnemos_auto_collect_status"]
        T11["mnemos_stats"]
    end

    subgraph MGR_B["MemoryManager — manager.py"]
        M1["add()"]
        M2["search()"]
        M3["recall_context()"]
        M4["agent_recall()"]
        M5["list_recent()"]
        M6["list_tags()"]
        M7["ingest_url()"]
        M8["watch_start/stop/status()"]
        M9["stats()"]
        AC_T["_checkpoint_tracker\nauto_collect.py"]
    end

    T1 -->|"TagContract validation"| M1
    T2 --> M2
    T3 --> M3
    T4 --> M4
    T5 -->|"gcw:checkpoint → add()"| M1
    T6 --> M5
    T7 --> M6
    T8 --> M7
    T9 --> M8
    T10 --> AC_T
    T11 --> M9
```

### Policy engine

```mermaid
flowchart TD
    YAML["policies.yaml\n~/.mnemos/policies.yaml"]

    subgraph PE_B["Policy Engine — policy/"]
        SCH["scheduler.py\nAPScheduler cron / interval"]
        TRIG["triggers.py\nevent hooks on status change"]
        ENG["engine.py\nYAML rule evaluation"]
        DLQ_P["dlq.py\nDead-Letter Queue"]
    end

    subgraph ACT["Actions"]
        A1["trigger cluster"]
        A2["trigger synthesize"]
        A3["trigger publish"]
        A4["archive"]
        A5["alert — quota 90%"]
    end

    YAML --> ENG
    SCH -->|"fire"| ENG
    TRIG -->|"fire"| ENG

    ENG -->|"auto-publish rule"| A3
    ENG -->|"cluster threshold"| A1
    ENG -->|"low quality"| DLQ_P
    ENG -->|"age / size rule"| A4
    ENG -->|"quota rule"| A5

    DLQ_P -->|"retry (exp. backoff + jitter)"| A2
    DLQ_P -->|"max retries → discard"| A4
```

### Compaction detection signals (M7)

```mermaid
flowchart TD
    subgraph SIG["Auto-Collect Signals — auto_collect.py"]
        S1["① call_counter\ncalls_since_save >= N"]
        S2["② context_size\ntokens / limit >= 0.80"]
        S3["③ summary_marker\nregex on inbound messages"]
        S4["④ reference_drop\nagent stops citing earlier IDs"]
    end

    W["Configurable weights\nauto_collect.yaml"]
    COMP["composite_score\n= sum of weight_i x signal_i"]
    REC{"score >= 0.4\nor summary_marker?"}

    OK["recommendation: ok"]
    SAVE["recommendation:\nsave_checkpoint"]
    REM["warning reminder\nappended to next MCP response"]

    S1 & S2 & S3 & S4 --> COMP
    W --> COMP
    COMP --> REC
    REC -->|"yes"| SAVE --> REM
    REC -->|"no"| OK
```

## 15. References

- ai-brain repo: `/var/home/abyss/LABs/AI/ai-brain/`
- ai-brain knowledge-pipeline concept: `ai-brain/docs/knowledge-pipeline-concept.md` (v0.4 roadmap)
- GCW stub plugin: `GithubCopilotWorkflow/plugins/mnemos-integration/`
- GCW tag contract skill: `GithubCopilotWorkflow/skills/mnemos-tag-contract/SKILL.md`
