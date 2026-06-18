# 0010. GCW A2A is a "best-effort, not a hard dependency" backend

*Historical artifact — English only.*

- **Status**: Accepted
- **Date**: 2026-06-15
- **Deciders**: abyss, GCW Agent Architect, Mnemos Tech Lead

## Context

`mnemos-requirements.md` (from the GCW team) explicitly states:

> Mnemos НЕ является single point of failure для GCW. Если он упал — система работает,
> просто теряет cross-session persistence.

The contract is: GCW must continue working when Mnemos is down, slow, or returns
errors. The implication: every Mnemos call site in GCW must have a defined
fallback. Mnemos's job is to be **strictly better** than the fallback, not
**strictly required**.

## Decision

Mnemos commits to the following contract with GCW:

1. **Mnemos can be down**: GCW writes to file-based fallback
   (`~/.gcw/a2a-messages.jsonl`) and continues.
2. **Mnemos can be slow**: GCW times out at 2 seconds, logs a warning, and writes
   the turn later (async).
3. **Mnemos returns 5xx**: GCW retries 3× with exponential backoff, then falls
   back to file.
4. **Mnemos returns 4xx (validation)**: GCW logs the error, **does not** write
   the turn, and continues.

Mnemos's role is to be a **strict improvement** over the file-based fallback, not
a hard dependency. A GCW operator who runs the system without Mnemos still has a
working A2A pipeline.

## Consequences

**Positive**

- GCW v0.6.0 ships without a hard dep on Mnemos. Onboarding to GCW does not
  require running Mnemos.
- Mnemos outages do not cascade into GCW outages. The two systems are loosely
  coupled.
- File-based fallback is a clean **lower bound** — Mnemos must beat it on at
  least one axis (search, dedup, multi-session aggregation) to justify its
  existence.

**Negative**

- A user who runs Mnemos but never sets up the file-based fallback can lose
  data when Mnemos is down. Documented in the GCW runbooks.
- The 4xx "do not write" path means a malformed A2A message that Mnemos
  rejects is **silently dropped** in GCW. Mitigated by GCW logging the rejection
  and providing operator visibility.

**Neutral**

- The two-tier system (Mnemos + file fallback) is a known anti-pattern in
  microservice literature ("dual writes"). It is acceptable here because the
  fallback is a **last resort**, not the primary store.

## Alternatives considered

- **Strong-consistency sync between Mnemos and GCW state.** Rejected: the
  requirement explicitly forbids Mnemos as a hard dependency. Strong consistency
  would force Mnemos into the critical path.
- **Make Mnemos a write-through cache (with no fallback).** Rejected: violates
  the GCW team's stated constraint.
- **Make the file-based fallback write-through Mnemos, not the other way around.**
  Rejected: Mnemos is a server, not a sidecar. It cannot be guaranteed to be
  running.

## References

- `/var/home/abyss/LABs/Projects/Reserching/GithubCopilotWorkflow/docs/a2a/mnemos-requirements.md`
  §"Failure modes (как GCW обрабатывает)"
- `docs/a2a-sessions.md` § Failure modes
- ADR-0007 (A2A Sessions API design)
