# ADR 0022: Licensing Foundation — Apache-2.0 Core, Open-Core Monetization, FSL Triggers

**Status:** Accepted (owner decision, 2026-09-03)
**Deciders:** Owner (Korrnals), informed by Analytics Lead and
Marketing/Product research (two independent expert passes + primary-source
license research)
**Scope:** license of the open mnemos family (server, pi-mnemos npm channel,
bundled model artifacts, future open packages), monetization policy,
relicensing triggers

## Context

The owner is a first-time monetizer who wants the community to remain free
and unimpeded while retaining the right to monetize later — via a paid
product, the future GUI "mnemos-eyes", support, or other channels. The
decision must be made BEFORE the project gains external users and before any
promotion, so the license is part of the founding story rather than a later
rule change.

State at decision time: `mnemos-memory-server` live on PyPI at 3.2.0 (MIT),
npm `pi-mnemos` 3.2.0 (MIT), zero installed base, all commit authors are the
owner (sole copyright holder; bot authors carry no copyrightable content).
The v4.0.0 tag had been cut under MIT but never distributed (no PyPI, no
npm, no wheel assets, no container image) — re-cutting it under the new
license publishes no "changed" code to any consumer.

Three options were examined in depth: stay MIT, adopt FSL-1.1-MIT (Fair
Source, auto-MIT after 2 years), adopt AGPL-3.0.

## Decision

**Apache-2.0 for the entire open family from v4.0.0 (re-cut). MIT for all
versions ≤ 3.2.0 forever (per-version license fixity). Open-core
monetization with an explicit trigger contract for any future license
change.**

### Why Apache-2.0 over MIT

1. **Niche norm.** The direct peer group — mem0, letta, cognee, chroma,
   qdrant, graphiti, fastmcp — is uniformly Apache-2.0. Corporate adopters
   arrive with Apache pre-approved on OSS policy lists; MIT's patent
   silence invites the one extra legal question during embedding review.
2. **Patent grant fits the product shape.** mnemos is an ML-adjacent server
   with a bundled ONNX model, designed to be embedded into third-party
   commercial agent stacks. Apache §3's irrevocable patent grant + patent
   retaliation clause is free insurance for a maintainer with no patent
   portfolio and reassurance for every embedder.
3. **Trademark carve-out (§6) + NOTICE slot.** The explicit trademark
   reservation protects the "mnemos" brand (future mnemos-eyes) against
   forks of the permissive snapshots; the NOTICE file conventionally
   documents bundled-model provenance (mnema-embed-v1 is distilled
   in-project from the Apache-2.0 teacher
   paraphrase-multilingual-MiniLM-L12-v2 — no third-party weights bundled).

### Why NOT the alternatives

- **FSL-1.1-MIT** (the initial recommendation of the first research pass):
  the non-compete protects against third-party paid clones of the core —
  but that threat only activates at scale (≈10k stars / tens of thousands
  of monthly downloads). At zero installed base the binding constraint is
  adoption velocity, and FSL costs listing placements (awesome-selfhosted
  non-free list), the "open source" label, and launch-narrative goodwill.
  Every precedent of relicensing damage (OpenTofu, Redis/Valkey) involved
  surprise timing at scale; a later switch framed as Fair Source with
  per-version fixity and advance notice is survivable (Sentry precedent).
- **AGPL-3.0**: verified rejection at Google ("MUST NOT be used") and
  routine corporate legal-team friction directly attack the embed-in-agent
  adoption path; it does not even protect a separate closed GUI.
- **Stay MIT**: acceptable but strictly dominated by Apache-2.0 here — the
  patent grant and trademark carve-out are free upgrades; the only cost is
  a longer license text.

### Monetization model (open-core)

The copyright holder is not bound by the license, so all monetization
channels survive any permissive core: the closed-source GUI product
(mnemos-eyes, separate private repository — GUI code never lands in the
open core), paid support, sponsorships (`.github/FUNDING.yml`), and future
paid add-ons. The only channel a permissive core excludes is third-party
commercial licensing of the core itself — ~$0 at any stage.

### Trigger contract (revisit FSL / commercial relicensing only when)

| Trigger | Threshold | Action |
| --- | --- | --- |
| T1 scale | ≥10k GitHub stars OR ≥25k monthly pip downloads | re-run the license decision |
| T2 product | mnemos-eyes GA − 1 month | documented relicense/dual-license decision point |
| T3 contributors | first external contributor of standing (~50 non-trivial commits) | adopt CLA (or patches-only policy) IMMEDIATELY — relicensing requires contributor consent after this point |

The FSL switch path, if ever taken: per-version fixity (existing releases
keep their license), advance-notice announcement, auto-MIT clock visible in
the README.

## Consequences

- LICENSE = Apache-2.0 (Copyright 2026 Korrnals); NOTICE added documenting
  the bundled mnema-embed-v1 provenance; pyproject `license = "Apache-2.0"`;
  npm `license` field updated; README badge and license section updated
  (EN/RU).
- The v4.0.0 tag is re-cut under Apache-2.0; the previously MIT-tagged
  v4.0.0 was never distributed (0 assets, absent from PyPI/npm), so no
  consumer ever received MIT 4.0.0 code. Versions ≤ 3.2.0 remain MIT.
- GitHub Sponsors enabled via `.github/FUNDING.yml`.
- Future companion packages (models, training tooling) default to
  Apache-2.0 for family consistency.