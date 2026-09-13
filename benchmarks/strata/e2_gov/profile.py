"""E2 stratum profile — fingerprint, class counts, distribution check.

E0 §3.3: "Corpus fingerprint (seed, version, class counts) committed
with E2; any corpus or issuance-path change → event-driven re-baseline
in the same PR." This module is that fingerprint for the E2 lanes
strata: sha256 over the stratum-defining module BYTES in fixed order
(same discipline as the S1 ``corpus_fingerprint``), committed as
``profile.json`` next to the modules and pinned by test — any edit to a
stratum module without re-recording the profile fails the suite.

Distribution pin (E0 §3.3, registered tolerance ± 2 pp):

* the COMBINED experimental corpus (golden 81 + governance 100 +
  checkpoint padding 240 = 421 entries) carries a checkpoint share of
  57.96% — the live store's 58% drowning profile (839 of 1457);
* the EXTENSION's own rules : decisions ratio (12 : 88) mirrors the
  live governance ratio (30 : 217 ≈ 1 : 7.2).

The profile records the honest scope of the mirror: the golden judge
corpus is fixed (byte-pinned by the S1 fingerprint), so combined-class
shares other than checkpoints are not free parameters — the checkpoint
share is the registered pin, the rules : decisions ratio is mirrored at
the extension level where E2 authors content.

No RNG exists anywhere in the stratum ("seed": deterministic
index-driven combinatorics), so the profile pins generator version +
module bytes, not a random seed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from benchmarks.corpus.corpus import CORPUS
from benchmarks.strata.e2_gov import checkpoints as checkpoints_mod
from benchmarks.strata.e2_gov import ground_truth as ground_truth_mod
from benchmarks.strata.e2_gov import queries as queries_mod
from benchmarks.strata.e2_gov import records as records_mod

#: Bumped when the stratum's shape semantics change (never silently —
#: a bump invalidates the recorded profile and the pin test fails).
STRATUM_VERSION = "e2-gov-1"

PROFILE_PATH = Path(__file__).resolve().parent / "profile.json"

#: E0 §3.3 registered distribution pins.
CHECKPOINT_SHARE_TARGET = 0.58
CHECKPOINT_SHARE_TOLERANCE = 0.02  # ± 2 pp (registered E0 fill)

#: The live store that produced the measured drowning (E0 §3.3):
#: 1457 entries — 839 checkpoints (58%), 30 rules, 217 decisions.
LIVE_STORE_TOTAL = 1457
LIVE_STORE_CHECKPOINTS = 839
LIVE_STORE_RULES = 30
LIVE_STORE_DECISIONS = 217

#: Governance classes (E0 §2.4 metric b): a block of either class in a
#: G-neg top-5 is a false insertion by construction.
GOVERNANCE_SUBTYPES: frozenset[str] = frozenset({"rule", "decision"})


def _stratum_modules() -> tuple[Any, ...]:
    return (records_mod, checkpoints_mod, queries_mod, ground_truth_mod)


def corpus_fingerprint() -> str:
    """sha256 over the stratum-defining module bytes (fixed order)."""
    digest = hashlib.sha256()
    for module in _stratum_modules():
        assert module.__file__ is not None
        path = Path(module.__file__)
        digest.update(path.name.encode())
        digest.update(b"\x00")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _class_counts(entries: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        for subtype in entry.mnemos_tags:
            counts[subtype] = counts.get(subtype, 0) + 1
    return counts


def experimental_corpus() -> list[Any]:
    """The combined E2 experimental corpus, fixed order.

    Golden 81 (the S1 judge corpus, byte-pinned upstream) + the 100
    seeded governance records + the 240 checkpoint padding = 421
    entries. This is the corpus an E3 runner ingests; nothing else in
    this wave consumes it.
    """
    return [*CORPUS, *records_mod.GOV_RECORDS, *checkpoints_mod.CHECKPOINT_ENTRIES]


def build_profile() -> dict[str, Any]:
    """Compute the full profile dict (also the ``profile.json`` schema)."""
    extension = [*records_mod.GOV_RECORDS, *checkpoints_mod.CHECKPOINT_ENTRIES]
    combined = experimental_corpus()
    ext_counts = _class_counts(extension)
    combined_counts = _class_counts(combined)
    checkpoint_share = combined_counts.get("checkpoint", 0) / len(combined)
    gov_rules = ext_counts.get("rule", 0)
    gov_decisions = ext_counts.get("decision", 0)
    return {
        "stratum_version": STRATUM_VERSION,
        "e0_spec": "docs/experiments/e0-meta-level.md §3.1-§3.3, §4.3-§4.4",
        "corpus_fingerprint": corpus_fingerprint(),
        "seed": "deterministic (index-driven combinatorics, no RNG)",
        "counts": {
            "gov_records": len(records_mod.GOV_RECORDS),
            "gov_rules": gov_rules,
            "gov_decisions": gov_decisions,
            "checkpoint_padding": len(checkpoints_mod.CHECKPOINT_ENTRIES),
            "extension_total": len(extension),
            "combined_total": len(combined),
            "analyzed_records": len(records_mod.ANALYZED_GOV_SLUGS),
            "analyzed_queries": len(queries_mod.ANALYZED_GOV_QUERIES),
            "replacement_pool": len(records_mod.REPLACEMENT_POOL_SLUGS),
            "gov_query_definitions": len(queries_mod.GOV_QUERIES),
            "neg_queries": len(queries_mod.NEG_QUERIES),
        },
        "distribution": {
            "checkpoint_share": round(checkpoint_share, 6),
            "checkpoint_share_target": CHECKPOINT_SHARE_TARGET,
            "checkpoint_share_tolerance": CHECKPOINT_SHARE_TOLERANCE,
            "extension_rules_decisions_ratio": round(gov_rules / gov_decisions, 4),
            "live_rules_decisions_ratio": round(LIVE_STORE_RULES / LIVE_STORE_DECISIONS, 4),
            "live_store": {
                "total": LIVE_STORE_TOTAL,
                "checkpoints": LIVE_STORE_CHECKPOINTS,
                "rules": LIVE_STORE_RULES,
                "decisions": LIVE_STORE_DECISIONS,
            },
            "note": (
                "checkpoint share is the registered pin (58% ± 2pp on the "
                "COMBINED corpus); the rules:decisions ratio mirrors the live "
                "ratio at the EXTENSION level — the golden judge corpus is "
                "byte-pinned by the S1 fingerprint and not a free parameter"
            ),
        },
        "class_counts": {"extension": ext_counts, "combined": combined_counts},
        "re_baseline_decision": {
            "s1_rebaseline_required": False,
            "reason": (
                "strata modules are outside the S1 corpus_fingerprint module "
                "set; the S1 stand's measured corpus is byte-identical "
                "(c2ce056d…) — bench-s1 keeps measuring what it measured"
            ),
        },
    }


def record_profile() -> dict[str, Any]:
    """(Re)write the committed ``profile.json`` from the live modules."""
    profile = build_profile()
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, sort_keys=False) + "\n")
    return profile


def load_profile() -> dict[str, Any]:
    profile: dict[str, Any] = json.loads(PROFILE_PATH.read_text())
    return profile


def distribution_ok(profile: dict[str, Any] | None = None) -> bool:
    """The registered distribution pin: 58% checkpoints ± 2 pp."""
    prof = profile if profile is not None else build_profile()
    share = float(prof["distribution"]["checkpoint_share"])
    return abs(share - CHECKPOINT_SHARE_TARGET) <= CHECKPOINT_SHARE_TOLERANCE + 1e-9


def main() -> int:
    profile = record_profile()
    status = "OK" if distribution_ok(profile) else "OUT OF TOLERANCE"
    print(
        f"e2-gov profile: fingerprint={profile['corpus_fingerprint'][:12]}… "
        f"checkpoints={profile['distribution']['checkpoint_share']:.4f} ({status}) "
        f"→ {PROFILE_PATH}"
    )
    return 0 if distribution_ok(profile) else 1


if __name__ == "__main__":
    raise SystemExit(main())
