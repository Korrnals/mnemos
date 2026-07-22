# mnemos-mesh Phase 3 contract — protobuf schemas

Contract-first definition of the mnemos-mesh federation APIs (GitHub issue
#105). This directory holds the `.proto` files that are the **source of
truth** for both the future Go binary (mnemos-mesh) and the future Python
gRPC client in mnemos.

## Contract-first principle

The flow is **Pydantic → proto → codegen**, not the reverse:

1. `mnemos/src/mnemos/compact.py::CompactRecord` (Pydantic) is the source
   of truth for the compact exchange record shape.
2. `federation.proto::CompactRecord` mirrors the Pydantic model field-by-field
   (id, type, title, summary, key_points, tags, source_agent, timestamp;
   reserved 9/10/11 for forward compat).
3. `buf.gen.yaml` produces Go + Python stubs from the `.proto` files.

The Pydantic model is never regenerated from the proto — it is hand-written
in Python and is the authoritative shape. The proto mirrors it; if the two
drift, **compact.py wins** and the proto is amended.

## Files

| File | Purpose |
|---|---|
| `federation.proto` | Peer-to-peer API (mesh A ↔ mesh B). `package mnemos.federation.v1`. Three RPCs: `Pull`, `SyncMetadata`, `Subscribe` (server streaming). Defines `CompactRecord`, `MetadataRecord`, `TriggerCodes`, and the Pull/Sync/Subscribe request/response messages. |
| `mnemos_core_api.proto` | mesh ↔ mnemos core API over Unix socket + gRPC. `package mnemos.core.v1`. Four RPCs: `ListMemories`, `WriteMemory`, `GetSubscriptionState`, `Heartbeat`. mnemos is the source of truth for storage + moderation; mesh is transport only. |

## SemVer policy

The contract is versioned independently of the mnemos Python package:

- The `.proto` packages carry a `v1` suffix (`mnemos.federation.v1`,
  `mnemos.core.v1`) — the **major** version is embedded in the package
  name, which is the protobuf-idiomatic way to signal a breaking change
  (a new `v2` package is a new API, not an in-place break).
- `v1.0.0` = first **stable** contract. Until then the contract is
  pre-stable and may change; the `v1` package suffix is reserved but
  breaking changes within `v1` are allowed (with a `BREAKING CHANGE:`
  footer in the commit, per the mnemos Git workflow).
- After `v1.0.0`: any wire-incompatible change is a **MAJOR** SemVer bump
  and is reflected by introducing a `v2` package — the old `v1` package
  stays in place so existing consumers keep compiling.

## buf breaking-change-detector

`buf.yaml` enables the breaking-change detector (the `breaking:` block).
In CI, `buf breaking --against <baseline>` compares the current module
against the default branch tip. Any wire-incompatible change (renumbered
field, type change, removed field without `reserved`, removed RPC) fails
CI before merge. Locally:

```bash
buf breaking mnemos/federation/proto --against .git#branch=main
```

This is the contract-first hard gate: a breaking change MUST be a conscious
MAJOR SemVer bump, not an accidental drift.

## Codegen

`buf.gen.yaml` configures two codegen targets from the same `.proto` source:

| Plugin | Output | Consumer |
|---|---|---|
| `buf.build/protocolbuffers/go` | `gen/go/` | Future mnemos-mesh Go binary |
| `buf.build/grpc/go` | `gen/go/` | Future mnemos-mesh Go binary (gRPC stubs) |
| `buf.build/protocolbuffers/python` | `gen/python/` | Future Python gRPC client in mnemos |

Run: `buf generate` (from this directory). Outputs land under `gen/`.

## Repo location — current vs future

The contract currently lives in the **mnemos** repo (`mnemos/federation/proto/`)
so the Python and Go sides can develop against it in lockstep. When the
contract stabilises to `v1.0.0` it will migrate to a dedicated
`github.com/Korrnals/mnemos-mesh` repository. The buf module name in
`buf.yaml` is already set to that forward target, so the migration is a
`git mv` + tag, not a rename + re-tag.

## See also

- Architectural contract: `../../.archcom/sessions/2026-07-17-federation-contract.md`
  (§2.3 Compact exchange format, §9 Trigger codes, §10 federation_access_log)
- Phase 1 context (safe channel, ADR-0016, mTLS, per-peer ACL):
  `../../.archcom/sessions/2026-07-20-automated-channel.md`
- Compact exchange format (Python source of truth):
  `../../src/mnemos/compact.py`
- ADR-0014 (bearer + TOTP, federation excluded):
  `../docs/project/adr/0014-api-auth-threat-model.md`
- Contract-first parallel development decision:
  mnemos memory `7fdb7f13`
- MetadataRecord (session 2 metadata-sync decision):
  mnemos memory `e88599b6`