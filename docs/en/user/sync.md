<!-- mnemos-integration: v2.0.0 -->
# Federation — Batch Sync (Phase 0)

**🌐 Language / Язык:** English · [Русский](../../ru/user/sync.md)

> Operator-curated, offline, cron-triggered batch sync between two
> mnemos instances. No network call is made by mnemos itself — transfer
> is out-of-band (rsync / scp / shared volume via `scripts/sync-peers.sh`).

---

## Overview

Batch sync lets two mnemos instances share memories that belong to a
curated set of **shared projects**. The flow is:

1. **Export** — `mnemos sync export` builds a `mnemos.federation.v1`
   compact payload from memories in the configured `shared_projects`,
   runs the moderation pipeline on each, and writes the result to a
   file (optionally AES-256-GCM encrypted).
2. **Transfer** — the operator moves the file to the target instance
   (rsync / scp / cp over a shared volume). `scripts/sync-peers.sh` is a
   cron-ready template that wraps all three steps.
3. **Import** — `mnemos sync import` reads the compact payload
   (decrypting if needed), validates each record, and merges
   idempotently by record `id`.

This is **Phase 0** of the federation roadmap (ArchCom 2026-07-17
federation contract §3.1): offline, operator-driven, no live network
protocol. Phase 2 (mediated pull) builds on the same compact format and
moderation pipeline.

---

## Configuration

Batch sync is governed by the `federation` section of `config.yaml`:

```yaml
federation:
  shared_projects:
    - project-umbra
    - project-mnemos
  moderation_mapping_ttl_hours: 24   # in-memory moderation mapping TTL
  moderation_refuse_threshold: 0.8   # >80% redacted → refuse
```

| Field | Default | Purpose |
|-------|---------|---------|
| `shared_projects` | `[]` (empty) | Whitelist of project slugs eligible for sync. Empty = no projects sync. |
| `moderation_mapping_ttl_hours` | `24` | TTL for the per-run moderation mapping table (in-memory only, never persisted). |
| `moderation_refuse_threshold` | `0.8` | Fraction of content that must be redacted/anonymized to trigger a `refuse` verdict. |

You can override `shared_projects` on a single run with
`--shared-projects` (space- or comma-separated) — the CLI value wins
over the config value.

---

## Export — `mnemos sync export`

```bash
mnemos sync export \
  --output /var/tmp/mnemos-sync.json \
  --shared-projects "project-umbra project-mnemos"
```

Options:

| Option | Default | Purpose |
|--------|---------|---------|
| `--output` / `-o` | `mnemos-sync.json` | Output file path (absolute recommended). Parent dirs are created. |
| `--encrypt` | off | Encrypt the payload with AES-256-GCM. Passphrase read from `MNEMOS_EXPORT_PASSPHRASE`. |
| `--shared-projects` | config `federation.shared_projects` | Space/comma-separated project slugs (overrides config). |
| `--dry-run` | off | Build the payload and print the summary; do NOT write the file. |
| `--config` / `-c` | discovery | Path to `config.yaml`. |

What the export does:

1. Resolves `shared_projects` (CLI arg > config; both empty → error).
2. Queries memories: `project` in `shared_projects`, excludes
   `mnemos:no-federate` and `archived` records.
3. Calls `build_compact_payload()` — runs the moderation pipeline
   (Layer 3) on each memory: `allow` → original content, `redact` →
   sanitized content, `refuse` → record excluded and counted.
4. Writes the compact payload
   (`{"schema": "mnemos.federation.v1", "records": [...], "stats": {...}}`)
   to `--output`, optionally encrypted.

Output summary:

```
✓ Exported: 12 records
  refused: 1
  secrets_redacted: 3
  pii_anonymized: 2
  encrypted: false
  shared_projects: project-umbra, project-mnemos
  path: /var/tmp/mnemos-sync.json
```

### Encryption

`--encrypt` reads the passphrase from the `MNEMOS_EXPORT_PASSPHRASE`
environment variable — never from a CLI argument (arguments appear in
process listings and shell history). If the env var is not set, no file
is written and the command exits with an error.

```bash
export MNEMOS_EXPORT_PASSPHRASE="your-passphrase-here"
mnemos sync export --output sync.enc --encrypt
```

The encrypted file carries an `MNEMOS1` magic header so the import side
can detect it automatically.

---

## Import — `mnemos sync import`

```bash
mnemos sync import /var/tmp/mnemos-sync.json
```

Options:

| Option | Default | Purpose |
|--------|---------|---------|
| `--passphrase-env` | `MNEMOS_EXPORT_PASSPHRASE` | Name of the env var holding the decryption passphrase (the **name**, not the value). |
| `--dry-run` | off | Validate the payload and report; do NOT write. |
| `--config` / `-c` | discovery | Path to `config.yaml`. |

What the import does:

1. Reads the file. If encrypted (magic header or `.enc` extension),
   reads the passphrase from the env var named by `--passphrase-env`
   (falls back to `MNEMOS_EXPORT_PASSPHRASE`).
2. Parses JSON, validates `schema == "mnemos.federation.v1"`, parses
   each record into a `CompactRecord`.
3. Validates each record (reuses the #86 import validation — content
   max length, tag contract, title length, schema drift, prompt-injection
   warnings). On any error, the **whole batch is rejected** (no partial
   writes).
4. Merges idempotently by record `id` (`fed:<source_agent>:<uuid>`):
   existing records are **skipped** (never overwritten); new records
   are created with `MemorySource.MCP`.

Output summary:

```
✓ Imported: 11 records
  skipped: 1
  format_version: mnemos.federation.v1
```

### Idempotency

Re-importing the same file is safe: every record carries a
`fed:<source_agent>:<uuid>` id. The second import finds each record
already present and skips it — no duplicates, no overwrites. This makes
cron-driven sync safe to re-run.

---

## `scripts/sync-peers.sh` — cron template

A cron-ready shell template that wires export → transfer → import
together. Set the env vars, then run it. Not executable without
configuration.

Required env vars:

| Var | Purpose |
|-----|---------|
| `SOURCE_MNEMOS_DIR` | Path to the source mnemos repo (with `.venv`). |
| `TARGET_MNEMOS_DIR` | Path to the target mnemos repo (with `.venv`). |
| `SHARED_PROJECTS` | Space-separated project slugs to sync. |

Optional env vars:

| Var | Default | Purpose |
|-----|---------|---------|
| `ENCRYPT` | `0` | `1` to encrypt the export. |
| `MNEMOS_EXPORT_PASSPHRASE` | — | Required when `ENCRYPT=1`. |
| `TRANSFER_METHOD` | `cp` | `rsync` / `scp` / `cp`. |
| `TRANSFER_DEST_HOST` | — | Target host for `rsync`/`scp`. |
| `SYNC_FILE` | `/tmp/mnemos-sync-<ts>.json` | Export file path. |
| `DRY_RUN` | `0` | `1` for end-to-end dry-run. |
| `SOURCE_CONFIG` / `TARGET_CONFIG` | discovery | Per-side `config.yaml` paths. |

Crontab example (hourly encrypted sync to a peer host over scp):

```cron
0 * * * * SOURCE_MNEMOS_DIR=/opt/mnemos-a TARGET_MNEMOS_DIR=/opt/mnemos-b \
          SHARED_PROJECTS="project-umbra project-mnemos" \
          MNEMOS_EXPORT_PASSPHRASE="$PASS" ENCRYPT=1 \
          TRANSFER_METHOD=scp TRANSFER_DEST_HOST=peer.example.com \
          /opt/mnemos-a/scripts/sync-peers.sh >> /var/log/mnemos-sync.log 2>&1
```

For `rsync`/`scp` transfers to a remote peer, the script prints the
exact `mnemos sync import` command to run on the peer (it cannot ssh in
and run the target venv itself). For `cp` (local same-host sync), the
script runs the import step directly.

---

## Audit log

Every `mnemos sync export` and `mnemos sync import` appends one JSONL
entry to `~/.mnemos/logs/sync-audit.jsonl`. The log is append-only —
`tail -f` for live monitoring, `jq` for aggregates, or ship to a SIEM.

Entry shapes (counters **only** — no raw content, no secrets, no PII):

```json
{"timestamp": "2026-07-19T10:00:00Z", "action": "sync-export", "output": "/var/tmp/mnemos-sync.json", "records_exported": 12, "records_refused": 1, "secrets_redacted": 3, "pii_anonymized": 2, "encrypted": false, "shared_projects": ["project-umbra", "project-mnemos"]}
{"timestamp": "2026-07-19T10:05:00Z", "action": "sync-import", "source": "/var/tmp/mnemos-sync.json", "records_imported": 11, "records_skipped": 1, "errors": [], "warnings": [], "encrypted": false, "format_version": "mnemos.federation.v1"}
```

The audit log is the operational trail: which projects synced, how many
records exported / refused / redacted, which imports failed. **Raw
content and secret values never enter the audit log** — only counters,
paths, and status flags.

---

## `mnemos:no-federate` exclusion

Records tagged `mnemos:no-federate` are excluded from sync export
entirely. The tag is auto-added on write by the Layer 1 secrets scanner
(#86) when a secret pattern is detected; owners can remove it with
explicit confirmation via `MemoryManager.remove_no_federate()`. See
[Tag Contract — `mnemos:no-federate`](./tag-contract.md#mnemosno-federate--federation-exclusion-marker)
for the full lifecycle.

Even without the tag, the moderation pipeline (Layer 3) runs on every
record at export time and refuses records whose content is mostly
secrets/PII — defence-in-depth so a single missed layer does not leak a
secret. See [Security — Federation defence-in-depth](../admin/security.md#11-federation-defence-in-depth).

---

## See also

- [Export & Import](./export-import.md) — full backups (JSON / SQLite).
- [Security — Federation defence-in-depth](../admin/security.md#11-federation-defence-in-depth) — the three-layer model.
- [Tag Contract — `mnemos:no-federate`](./tag-contract.md#mnemosno-federate--federation-exclusion-marker) — the exclusion marker.
- [MCP Tools](./mcp-tools.md) — `mnemos_export` / `mnemos_import` MCP tools (the MCP surface for full export/import).