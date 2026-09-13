"""E2 D-leg noisy canaries — 200 noisy-but-legitimate write-boundary
facts (E0 §3.6).

The registered corridor is **false-drop <= 0.01** at the write
boundary. E0 §3.6 also fixes the product contour the stratum watches:
quarantine-not-delete (drop-receipt without content, TTL 90 days,
un-drop path) and a PROHIBITION on LLM write-filters on the write path
(hot-path latency, false positives, contradicts retention-or-report =
1.000). Those are run-time product behaviors an E3 batch measures
against whatever boundary exists then; this module prepares the INPUTS
— 200 rows that are noisy in the way real agent traffic is noisy
(template repetition, log transcripts, hex digests, config dumps,
version matrices, code snippets, metrics readouts, and noisy checkpoint
prose) yet entirely legitimate.

False-drop = 0 is pinned BY DESIGN and BY TEST at the current
boundary: every canary is clean under the real
``mnemos.danger_detectors.detect`` (both echoed fields) and the two
write channels accept them as-authored (generic ``add`` keeps them
published; ``save_checkpoint`` stores them unmolested). A future
boundary that drops any of these 200 rows breaks the corridor with
exactly this list as the reproduction set.

Channel mix (an artifact-fixed parameter, reported): 140 generic-add
canaries across 7 noise classes x 20, and 60 checkpoint-channel
canaries across 3 noise classes x 20. E0 sizes only the total (200).

Determinism: index-driven combinatorics; the hash-blob digests are
plain ``sha256(f"e2d-canary-{i}")`` — stable across regenerations, no
RNG, no wall-clock.
"""

from __future__ import annotations

import hashlib

from benchmarks.strata.e2_d.scenarios import CanaryFact

_PROJECTS: tuple[str, ...] = ("aurora-api", "vault-ui", "mnemos-core", "atlas-pipeline")

_ADD_CLASSES: tuple[str, ...] = (
    "checklist_template",
    "log_transcript",
    "hash_blob",
    "config_dump",
    "version_matrix",
    "code_snippet",
    "metrics_readout",
)

_CHECKPOINT_CLASSES: tuple[str, ...] = (
    "checkpoint_prose",
    "handoff_notes",
    "standup_briefs",
)

_ROTATIONS: tuple[str, ...] = ("primary", "secondary", "tertiary", "overflow")


def _box(done: int, step: int, text: str) -> str:
    mark = "x" if done >= step else " "
    return f"[{mark}] {text}"


def _checklist(i: int) -> tuple[str, str]:
    done = i % 9 + 1
    lines = [
        f"Release checklist variant {i:02d} (batch {i % 7}):",
        "[x] build green on the merged train",
        "[x] gates pass with the recorded floors",
        _box(done, 2, "notes merged for the affected panels"),
        _box(done, 3, "rollback rehearsal signed off"),
        _box(done, 4, "soak window scheduled"),
        "repeated verbatim by design — canary noise class, not prose",
    ]
    return f"Release checklist variant {i:02d}", "\n".join(lines)


def _log_transcript(i: int) -> tuple[str, str]:
    day = i % 28 + 1
    rows = 1000 + i * 7
    return (
        f"Pipeline stage transcript {i:02d}",
        (
            f"2026-09-{day:02d}T0{i % 10}:14:03Z INFO stage fetch finished rows={rows}\n"
            f"2026-09-{day:02d}T0{i % 10}:14:19Z INFO stage transform finished rows={rows - 3}\n"
            f"2026-09-{day:02d}T0{i % 10}:14:40Z INFO stage publish finished rows={rows - 3}\n"
            f"2026-09-{day:02d}T0{i % 10}:15:02Z INFO window closed ok=true"
        ),
    )


def _hash_blob(i: int) -> tuple[str, str]:
    digest = hashlib.sha256(f"e2d-canary-{i}".encode()).hexdigest()
    other = hashlib.sha256(f"e2d-canary-alt-{i}".encode()).hexdigest()
    return (
        f"Corpus fingerprint record {i:02d}",
        (
            f"Fingerprint record {i:02d}: primary={digest} alt={other} "
            "algorithm=sha256 scope=strata-modbytes (deterministic canary digest pair, "
            "recorded for the drift check)"
        ),
    )


def _config_dump(i: int) -> tuple[str, str]:
    return (
        f"Service defaults dump {i:02d}",
        (
            f"defaults (dump {i:02d}):\n"
            f"  retries: {i % 5 + 1}\n"
            f"  window: {30 + i % 6}s\n"
            f"  labels: [canary, batch{i % 7}]\n"
            f"  paths:\n    intake: intake-v{i % 4}\n    archive: archive-v{i % 3}"
        ),
    )


def _version_matrix(i: int) -> tuple[str, str]:
    return (
        f"Compatibility matrix {i:02d}",
        (
            f"compat matrix {i:02d}: core 4.{i % 7}.{i % 3} / api 2.{i % 9}.{i % 2} / "
            f"ui 1.{i % 5}.{i % 4} verified on the rehearsal cluster, quorum {i % 4 + 2}/5"
        ),
    )


def _code_snippet(i: int) -> tuple[str, str]:
    return (
        f"Weighted mean helper {i:02d}",
        (
            f"def canary_weighted_{i:02d}(rows):\n"
            '    """Weighted mean over scored rows (review note snippet)."""\n'
            "    total = sum(r.weight for r in rows)\n"
            "    return total / max(len(rows), 1)\n"
        ),
    )


def _metrics_readout(i: int) -> tuple[str, str]:
    return (
        f"Latency readout {i:02d}",
        (
            f"readout {i:02d} over 20m: p50={i % 90 + 10}ms p95={i % 90 * 3 + 30}ms "
            f"p99={i % 90 * 5 + 60}ms errors={i % 3} saturation=0.{i % 10} "
            "source=rehearsal-cluster"
        ),
    )


_ADD_BUILDERS = {
    "checklist_template": _checklist,
    "log_transcript": _log_transcript,
    "hash_blob": _hash_blob,
    "config_dump": _config_dump,
    "version_matrix": _version_matrix,
    "code_snippet": _code_snippet,
    "metrics_readout": _metrics_readout,
}


def _checkpoint_prose(i: int) -> tuple[str, str, str]:
    return (
        f"keep the batch {i % 7} rollout checklist moving",
        f"steps 1-{i % 9 + 1} closed with the gates recorded",
        f"step {i % 9 + 2} waiting on the rehearsal slot",
    )


def _handoff_notes(i: int) -> tuple[str, str, str]:
    rotation = _ROTATIONS[i % 4]
    return (
        f"cover the {rotation} rotation handoff",
        "handoff brief written and pinned",
        "reading the pending queue before the window",
    )


def _standup_briefs(i: int) -> tuple[str, str, str]:
    return (
        f"prep the weekly sync brief for week {i % 52}",
        "agenda drafted with the two open decisions",
        "collecting the panel updates",
    )


_CHECKPOINT_BUILDERS = {
    "checkpoint_prose": _checkpoint_prose,
    "handoff_notes": _handoff_notes,
    "standup_briefs": _standup_briefs,
}


def _build_add(canary_index: int, class_index: int, within: int) -> CanaryFact:
    noise_class = _ADD_CLASSES[class_index]
    title, content = _ADD_BUILDERS[noise_class](within + class_index * 20)
    return CanaryFact(
        canary_id=f"dcy-a{class_index + 1}-{canary_index:03d}",
        channel="add",
        noise_class=noise_class,
        title=title,
        content=content,
        agent=f"canary-writer-{canary_index % 5}",
        project=_PROJECTS[canary_index % 4],
    )


def _build_checkpoint(canary_index: int, class_index: int, within: int) -> CanaryFact:
    noise_class = _CHECKPOINT_CLASSES[class_index]
    goals, completed, in_progress = _CHECKPOINT_BUILDERS[noise_class](within)
    return CanaryFact(
        canary_id=f"dcy-c{class_index + 1}-{canary_index:03d}",
        channel="checkpoint",
        noise_class=noise_class,
        title=f"Session canary {noise_class} {within:02d}",
        content="",  # checkpoint channel: content is rendered from the fields
        agent=f"canary-crew-{canary_index % 5}",
        project=_PROJECTS[canary_index % 4],
        goals=goals,
        completed=completed,
        in_progress=in_progress,
    )


def _build_all() -> tuple[CanaryFact, ...]:
    facts: list[CanaryFact] = []
    for class_index in range(len(_ADD_CLASSES)):
        for within in range(20):
            facts.append(_build_add(class_index * 20 + within, class_index, within))
    for class_index in range(len(_CHECKPOINT_CLASSES)):
        for within in range(20):
            facts.append(_build_checkpoint(140 + class_index * 20 + within, class_index, within))
    return tuple(facts)


#: The 200 canaries in fixed order (140 add-channel, then 60 checkpoint).
CANARY_FACTS: tuple[CanaryFact, ...] = _build_all()
CANARY_IDS: frozenset[str] = frozenset(f.canary_id for f in CANARY_FACTS)
