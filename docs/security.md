# Mnemos — Security Posture (M15.2)

> **Owner**: GCW Senior Security Engineer
> **Status**: Active — last reviewed 2026-06-15
> **Scope**: Mnemos memory & knowledge server (forked from ai-brain)
> **Out of scope**: M16 A2A Sessions API (new module, separate threat model)

This document captures the security-relevant design decisions of the Mnemos
codebase. It is the authoritative reference when triaging findings, writing
new tests, or reviewing pull requests that touch trust boundaries.

---

## 1. Threat model summary

Mnemos is a **local-first, single-tenant, file-backed** memory server
deployed as either a CLI tool, a stdio MCP server, or a loopback HTTP API
(defaults to `127.0.0.1`). It exposes:

- A SQLite database (`mnemos.db`) and an Obsidian-compatible vault.
- A local FastAPI HTTP API (default `127.0.0.1:8787`).
- An MCP server over stdio.
- Optional outbound network calls to LLM providers (Ollama / OpenAI / etc.)
  and to user-supplied URLs (`ingest_url`).

### Trust boundaries

| Boundary | Trust side | Untrusted side | Mitigations |
|----------|------------|----------------|-------------|
| `ingest_url` (HTTP fetch) | Mnemos process | Public Internet (any URL the user passes) | SSRF blocklist — see §2 |
| HF Hub download (`ONNXHubProvider`) | Mnemos process | HuggingFace Hub | Pinned `revision=` (CWE-494) — see §3 |
| MCP stdio | Mnemos process | Local GCW agent | Unix permission boundary, no auth needed (loopback) |
| FastAPI HTTP API | Mnemos process | Local processes (loopback) | Loopback bind by default; no remote surface in v1 |
| FTS5 search (`fts_search`) | Mnemos process | End-user query string | FTS5 escape — see §4 |
| `update_fields` dynamic SQL | Mnemos process | `**kwargs` from callers | Whitelisted column dispatch — see §5 |
| `0.0.0.0` listener string | Mnemos process | bandit B104 (false positive) | `# nosec B104` with justification — see §6 |

### Out of scope for v1

- Multi-tenant auth (only single-tenant / single-user deployments).
- TLS / mutual auth (loopback-only surface in v1).
- Vault-at-rest encryption (deferred to a post-M15 hardening phase).
- Rate limiting on MCP endpoints (deferred).

---

## 2. SSRF prevention (`MemoryManager._validate_url`)

The `ingest_url` method can fetch any URL the user supplies. Without
controls, an attacker can pivot through Mnemos to reach loopback or
cloud-metadata endpoints.

**Blocklist** (must stay current — see advisory list below):

- Schemes: only `http`, `https` accepted (rejects `file:`, `gopher:`, etc.).
- Hostnames: `localhost`, `127.0.0.1`, `0.0.0.0`, `::1`,
  `169.254.169.254` (AWS / GCP / Azure metadata).
- CIDR ranges: `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`.

**Followed redirects?** Yes, with a hard cap of 5 hops (v2 posture, T5-SSRF).
Redirects are followed **manually** with `httpx.Client(follow_redirects=False)`.
Every `Location` target is passed through `_validate_url` before the next
request is issued (per-hop guard). This closes the open-redirect pivot where
a public host returns 30x to an internal or metadata endpoint that would
otherwise bypass the initial URL check. Exceeding the hop limit or a loop
detection results in a fetch-failed placeholder (no exception surfaces to
the caller).

**Tests**: `tests/test_security.py::TestUrlValidation`. Includes
`169.254.169.254` (AWS metadata) and the full RFC1918 ranges.

---

## 3. Supply chain — HF Hub pinning (M15.2, B615)

`ONNXHubProvider` downloads ONNX model files from HuggingFace Hub.
Without `revision=`, every call pulls the latest commit on the default
branch — a compromised or replaced model would be picked up silently.
This is **CWE-494** (download of code without integrity check).

**Mitigations (defence in depth)**:

1. **Pinned revision (primary)** — `EmbeddingConfig.hf_revision` defaults
   to a specific commit SHA. Every `hf_hub_download` call must pass
   `revision=` (the code path in `ONNXHubProvider.__init__` raises
   `ValueError` if the operator does not provide one).
2. **Configurable** — the SHA can be overridden via
   `MNEMOS_EMBEDDING__HF_REVISION` env var or `config.yaml`. Operators
   changing `embedding.model` MUST also update `embedding.hf_revision`
   to a matching pinned SHA.
3. **SHA256 verification (planned, not yet implemented)** — TODO for a
   follow-up phase. The HF Hub response should be hashed and compared
   against an expected digest stored alongside the pinned revision.

**Tests**: `tests/test_security.py::TestHfHubPinning` mocks
`huggingface_hub.hf_hub_download` and asserts `revision=` is in the
kwargs of every call.

---

## 4. FTS5 injection prevention (M15.2, B608)

`SQLiteStore.fts_search` runs `MATCH` against user-supplied query text.
FTS5 has a rich query syntax (`*`, `NEAR`, column filters via `:`)
that turns naive interpolation into a powerful injection vector:
e.g. `'" OR col:"content'; DROP TABLE memories; --'` would not execute
the DROP (FTS5 is read-only), but would cause data exfiltration or
errors that leak schema information.

**Mitigation** — `_build_fts_query`:

1. Strip FTS5 special characters: `* " ' ( ) :`.
2. Collapse whitespace.
3. Wrap the result in double quotes — FTS5 treats the contents of a
   double-quoted string as a literal phrase with **no operator parsing**.

The fix is documented in the SQLite reference:
<https://www.sqlite.org/fts5.html#fts5_strings>.

The SQL body itself is now built by concatenating static fragments and
`?` placeholders, so no user input flows into the statement.

**Tests**: `tests/test_security.py::TestFts5Escaping` feeds hostile
strings (`" OR col:"content`, `'"; DROP TABLE ...`, etc.) and asserts
the query returns no rows and does not raise.

---

## 5. Dynamic SQL — whitelisted column dispatch (M15.2, B608)

`SQLiteStore.update_fields` previously built the `UPDATE` statement with
an f-string of the form `f"UPDATE memories SET {setters} WHERE id=?"`,
where `setters` was filtered at runtime by an `allowed` set. Bandit
B608 flags this because static analysis cannot prove the filter is
exhaustive. A future maintainer widening `allowed` carelessly would
re-introduce the vulnerability.

**Mitigation** — module-level `_FIELD_UPDATERS` dict:

- Keys are the **only** column names `update_fields` will ever accept.
- Values are pre-baked SQL fragments (`"status=?"`, `"title=?"`, …).
- The `UPDATE` is built by joining the **dict values** (static strings),
  never user-supplied identifiers. Values are bound `?` parameters.

Adding a new column requires editing the dict AND the SQLite schema —
the two cannot drift silently.

**Tests**: `tests/test_security.py::TestSqlInjectionSafe::test_update_fields_rejects_unknown_columns`
and `...test_update_fields_no_fstring_injection` ensure malicious kwargs
are silently dropped and the table is never corrupted.

---

## 6. Network binding — `0.0.0.0` (M15.2, B104)

Bandit B104 flags the string literal `"0.0.0.0"` anywhere in code, on
the assumption that it is a socket bind. In Mnemos the string appears
**only inside an SSRF blocklist** (`MemoryManager._validate_url`) — it
is the *thing being rejected*, not a bind target. The actual API server
(`cli/main.py:serve`) defaults to `127.0.0.1`; an operator who
deliberately wants container port-mapping can pass `--host 0.0.0.0`,
and that path is also documented.

**Suppression** (per `lint-and-validate.instructions.md` — "When
suppression is acceptable"):

- `manager.py:379`: `# nosec B104 — blocklist entry, not a bind()`

This is a **confirmed false positive** of the bandit rule, annotated
with a one-line explanation at the suppression site. The same pattern
is used in `cli/main.py` if and when the operator passes `--host
0.0.0.0`.

---

## 7. Other controls already in place

These were introduced in earlier M-phases and are listed here for
completeness — they are **not** part of M15.2.

- **Path traversal** — `VaultManager._sanitize_filename` replaces `/`
  and `..` segments in vault filenames. `path_scoped.py` uses
  `Path.resolve()` to keep watchers inside the watched root.
- **M2 tag contract** — `models.py::validate_tag_contract` enforces
  the `project:` / `agent:` / `gcw:` prefix taxonomy at the MCP layer.
- **M9 SSRF guard** — `_validate_url` (covered in §2).
- **M6 traces** — every LLM-bound step is recorded with latency, token
  counts, and a `rationale_summary`. This is the audit log; no separate
  log infrastructure required for v1.

---

## 8. Verification

Run before merging any change that touches code, configuration, or
schema:

```bash
bandit -r src/                  # MUST: 0 issues (no skips)
pytest tests/test_security.py -v
pytest tests/ -q
ruff check src/ tests/          # MUST: 0 errors
```

If a new finding is intentionally suppressed, follow the suppression
contract from `.copilot/instructions/lint-and-validate.instructions.md`:

1. Confirm it is a false positive of the tool, not of the code.
2. One-line comment next to the suppression with the rule id and reason.
3. Scope limited to one line / one statement / one function.
