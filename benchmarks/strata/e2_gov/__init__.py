"""E2 lanes strata — G-gov / G-neg corpus wave (E0 §3.1-§3.3, §4.3-§4.4).

Corpus PREPARATION ONLY: no experiment legs, no assemble calls, no
metrics. The E3 runner (future wave) consumes this package; until then
nothing measures it except the structural tests that pin it. Import the
submodules directly (``records``, ``queries``, ``checkpoints``,
``ground_truth``, ``profile``, ``loader``) — deliberately no eager
re-exports here.

E0 section → artifact map (all counts test-pinned):

* §3.1 G-gov seeding — ``records.GOV_RECORDS``: 60-100 rules/decisions
  permitted; this wave seeds 100 (12 rules + 88 decisions).
* §3.1 analyzed lock — ``records.ANALYZED_GOV_SLUGS``: 48 records
  x 2 queries; this wave locks 48 (6 rules + 42 decisions).
* §3.1 replacement pool — ``records.REPLACEMENT_POOL_SLUGS``: the
  seeding surplus; this wave holds 52 records.
* §3.1 query pairs — ``queries.ANALYZED_GOV_QUERIES``: denominator 96;
  this wave defines 96 (48 records x ph/pr phrasings).
* §3.2 G-neg control — ``queries.NEG_QUERIES``: ~24 near-miss queries;
  this wave pins 24 with knowledge-only gold.
* §3.3 profile pin — ``profile.py`` + ``profile.json``: checkpoints
  58% ± 2pp on the combined corpus; realized 244/421 = 57.96%.
* §3.3 fingerprint — ``profile.corpus_fingerprint()``: sha256 over 4
  stratum modules, committed with E2, pinned by test.
* §4.3 blind setup — ``ground_truth.build_adjudication_worksheet()``:
  leg-stripped pairs plus a separate answer key.
* §4.4 rubric — ``ground_truth.ADJUDICATION_RUBRIC``: the 3 conjunctive
  criteria, carried verbatim in the worksheet.
* §4.4 replacement log — ``ground_truth.record_rejection`` +
  ``adjudication_ledger.json``: append-only, denominator stays 96.

Isolation contract (E0 §1.4, ADR-0020): the S1 stand's
``corpus_fingerprint`` hashes only ``benchmarks/corpus`` modules; this
package is invisible to it. ``make bench-s1`` therefore keeps measuring
exactly what it measured before this wave — the isolation is asserted
by test, and the re-baseline decision (not required for S1) is recorded
inside ``profile.json``.
"""
