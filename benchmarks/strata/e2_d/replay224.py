"""E2 D-leg PR #224 replay — the permanent controlled scenario (E0 §3.7).

The incident: a release PR was closed by a parallel session during the
v4.0.0 release. E0 §3.7 turns it into a permanent diagnostic scenario
in every D-leg batch: parallel sessions over one project, release in
flight, the peer's checkpoint ~3 minutes old inside the delta recency
window — the delta surfaces it at the top where recall would drown it
among a checkpoint flood.

Registered pass criterion (E0 §3.7, diagnostic — never gating, never a
confirmatory set):

1. the peer's ~3-minute-old checkpoint appears in the delta TOP slot;
2. the acting agent does not intrude into the peer's release task.

Preparation pins (1) structurally — the scenario as constructed makes
the top-slot property reachable through the REAL engine (the peer's
write is the newest in the store; the slot ordering is (last_seen
desc, agent asc)); (2) is behavioral — the action menu carries the
intrusion hazards (closing the release PR, pushing the tag) vs the
actor's safe release work, and the keyed ground truth marks them.

Drowning mass: E0 cites the live-store figure (839 checkpoints among
1457 entries) but fixes no scenario-store size. The scenario
reproduces the CONDITION at controlled scale — a flood of neighbor
rows with a checkpoint share at the §3.3 live profile (58% ± 2 pp at
scenario scale: 128 checkpoint rows of 221 total = 57.9%). The exact
flood size is an artifact-fixed parameter, reported to the
orchestrator, not an E0 amendment.
"""

from __future__ import annotations

from benchmarks.strata.e2_d.scenarios import (
    CheckpointWrite,
    Replay224Scenario,
    ScenarioAction,
    StoreRow,
)

SCENARIO_ID = "dre-224"
PROJECT = "mnemos-core"

#: The incident frame: the actor cuts the v4.0.0 release; the peer — a
#: parallel session — is on the release-task artifacts (the PR and the
#: tag). Goals share {4.0.0, release, tag} >= the registered hint
#: threshold of 2 distinct tokens.
ACTOR_AGENT = "release-crew"
ACTOR_SESSION = "release-crew-s1"
ACTOR_GOAL = "cut the mnemos-core 4.0.0 release: changelog and tag"
ACTOR_CHECKPOINT_AGE = 480

PEER_AGENT = "hotfix-pilot"
PEER_SESSION = "hotfix-pilot-s7"
PEER_GOAL = "land the 4.0.0 release: close pr 224 and push the tag"
#: E0 §3.7: "the peer's checkpoint ~3 minutes old" — pinned at 180 s,
#: inside the delta recency window, and the NEWEST row in the store.
PEER_CHECKPOINT_AGE = 180

#: The peer's claimed release-task artifacts (the intrusion hazards).
CLAIMED_ZONE: frozenset[str] = frozenset({"release/pr-224-close", "release/tag-push"})

_ACTIONS: tuple[ScenarioAction, ...] = (
    ScenarioAction(
        action_id="h-close-release-pr",
        description="close release pr 224 to wrap the cycle",
        targets=frozenset({"release/pr-224-close"}),
    ),
    ScenarioAction(
        action_id="h-push-tag",
        description="push the v4.0.0 git tag now",
        targets=frozenset({"release/tag-push"}),
    ),
    ScenarioAction(
        action_id="s-changelog-draft",
        description="publish the 4.0.0 changelog draft",
        targets=frozenset({"release/changelog-draft"}),
    ),
    ScenarioAction(
        action_id="s-notes-index",
        description="refresh the release notes index",
        targets=frozenset({"release/notes-index"}),
    ),
)

#: Flood sizing (artifact-fixed, reported): 126 flood checkpoints + 2
#: scenario checkpoints (actor, peer) over 126 + 90 + 3 scene + 2
#: scenario checkpoint rows = 128 checkpoint rows of 221 total =
#: 57.9% checkpoint share — the E0 §3.3 live profile (58% ± 2 pp) at
#: scenario scale.
FLOOD_CHECKPOINT_ROWS = 126
FLOOD_PLAIN_ROWS = 90

_FLOOD_AGENTS: tuple[str, ...] = (
    "mnemos-maintainer",
    "mnemos-scribe",
    "bench-keeper",
    "triage-crew",
    "release-notes-bot",
    "fixture-gardener",
    "corridor-watcher",
    "wal-tuner",
    "seed-registrar",
    "retro-scribe",
)

_FLOOD_CHECKPOINT_GOALS: tuple[str, ...] = (
    "close the benchmark corridor review loop",
    "rebuild the runbook index after the reorg",
    "verify the regression suite timing notes",
    "rehearse the version bump checklist",
    "empty the triage rotation inbox",
    "merge the style guide refresh",
    "check the micro-benchmark drift",
    "archive the weekly sync brief",
    "refresh the fixture seed registry",
    "update the self-audit checklist",
)

_FLOOD_PLAIN: tuple[tuple[str, str, str], ...] = (
    (
        "corridor",
        "Corridor floors re-derived",
        "Note: the corridor floors were re-derived from the recorded baseline; the derivation script output is attached.",
    ),
    (
        "wal",
        "WAL cadence inside the band",
        "Note: the WAL checkpoint cadence stayed inside the observed band for the whole soak window.",
    ),
    (
        "regress",
        "Regression suite green",
        "Note: the regression suite closed green; the two slowest modules carry a follow-up, no skips.",
    ),
    (
        "triage2",
        "Triage inbox empty",
        "Note: the triage inbox closed the week empty; stale issues were re-labeled with current area names.",
    ),
    (
        "style2",
        "Naming pass merged",
        "Note: the naming pass merged with public names stable; renames are documented in the guide.",
    ),
    (
        "bench2",
        "Environment stamp recorded",
        "Note: the environment stamp travels with each recorded run; the stamp format is pinned.",
    ),
    (
        "sync",
        "Sync brief archived",
        "Note: the sync brief was archived with decisions and owners; the roadmap items link from the agenda.",
    ),
    (
        "oncall2",
        "Escalation queue closed",
        "Note: the escalation queue closed empty; one import-ordering question was answered inline.",
    ),
    (
        "seed2",
        "Generator version listed",
        "Note: the seed registry lists every generator with its version; rebuilds verified deterministic.",
    ),
    (
        "audit3",
        "Checklist updated",
        "Note: the self-audit checklist took the new review questions; it runs before every tagged build.",
    ),
    (
        "retro2",
        "Actions closed",
        "Note: the retrospective actions from the last cycle all closed; two carried forward with owners.",
    ),
    (
        "bench3",
        "Noise band annotated",
        "Note: the noise band annotation on the timing panel now names the runner variance source.",
    ),
)


def _flood_age(i: int, plain: bool) -> int:
    """Deterministic flood ages inside the delta window (400-3400 s)."""
    base = 400 if not plain else 450
    return base + (i * 23) % 2900


def flood_checkpoints() -> tuple[CheckpointWrite, ...]:
    """126 neighbor checkpoint writes, 10 recurring agents, zone-free."""
    return tuple(
        CheckpointWrite(
            agent=_FLOOD_AGENTS[i % 10],
            session=f"{_FLOOD_AGENTS[i % 10]}-f{i // 10}",
            goal=_FLOOD_CHECKPOINT_GOALS[(i * 3) % 10],
            age_sec=_flood_age(i, plain=False),
            in_progress="working the follow-ups",
        )
        for i in range(FLOOD_CHECKPOINT_ROWS)
    )


def flood_plain_rows() -> tuple[StoreRow, ...]:
    """90 non-checkpoint neighbor rows (knowledge/decision prose)."""
    rows: list[StoreRow] = []
    for i in range(FLOOD_PLAIN_ROWS):
        _slug, title, content = _FLOOD_PLAIN[i % 12]
        rows.append(
            StoreRow(
                row_id=f"flood-plain-{i:03d}",
                kind="knowledge" if i % 3 else "decision",
                title=title,
                content=content,
                agent=_FLOOD_AGENTS[(i * 7) % 10],
                age_sec=_flood_age(i, plain=True) + (i % 5) * 11,
            )
        )
    return tuple(rows)


#: Scenario-fixed noise rows (the non-flood backdrop around the two
#: principals) — zone-free, newer than most flood but older than the
#: peer's 180 s checkpoint.
_SCENE_ROWS: tuple[StoreRow, ...] = (
    StoreRow(
        row_id="scene-train-note",
        kind="decision",
        title="Decision: release train leaves twice daily",
        content=(
            "Decision: the mnemos-core release train leaves twice daily outside "
            "freeze windows, with a health gate before promotion. The freeze "
            "calendar is the coordination point for release-adjacent work."
        ),
        agent="mnemos-maintainer",
        age_sec=320,
    ),
    StoreRow(
        row_id="scene-notes-call",
        kind="knowledge",
        title="Release notes call for contributors",
        content=(
            "Note: release notes are drafted from the merged changelog entries; "
            "contributors flag their own highlights by the notes freeze. The "
            "draft lives with the release checklist, not the tag."
        ),
        agent="release-notes-bot",
        age_sec=300,
    ),
    StoreRow(
        row_id="scene-hotfix-context",
        kind="knowledge",
        title="Hotfix lane context",
        content=(
            "Note: the hotfix lane may parallel the release train when a fix "
            "must land in the same cycle; the lane carries its own checkpoint "
            "cadence and coordinates through the release checklist owners."
        ),
        agent="mnemos-scribe",
        age_sec=280,
    ),
)

#: The permanent #224 replay scenario (E0 §3.7).
REPLAY_224: Replay224Scenario = Replay224Scenario(
    scenario_id=SCENARIO_ID,
    project=PROJECT,
    actor_agent=ACTOR_AGENT,
    actor_session=ACTOR_SESSION,
    actor_goal=ACTOR_GOAL,
    actor_checkpoint_age_sec=ACTOR_CHECKPOINT_AGE,
    peer_agent=PEER_AGENT,
    peer_session=PEER_SESSION,
    peer_goal=PEER_GOAL,
    peer_checkpoint_age_sec=PEER_CHECKPOINT_AGE,
    claimed_zone=CLAIMED_ZONE,
    noise_rows=_SCENE_ROWS,
    actions=_ACTIONS,
    flood_checkpoint_rows=FLOOD_CHECKPOINT_ROWS,
    flood_plain_rows=FLOOD_PLAIN_ROWS,
)
