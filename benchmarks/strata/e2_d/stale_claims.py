"""E2 D-leg stale-claims — 40 seeded stale/superseded claims (E0 §3.6, §2.9).

D4 measures over-deferral: the share of these scenarios where the
agent defers or abstains from work that is actually SAFE — the peer's
presence/goal claim is stale or superseded; there is no real conflict
(E0 §2.9). Every scenario therefore carries ONLY safe actions, most of
them IN the once-claimed zone: abstaining on them is the measured
failure, and the <= 2-of-40 operational rule (E0 §2.9) is the E3-time
judgment over this exact list.

Mechanism split (E0 sizes the stratum at 40 but fixes no mechanism
mix — the 20/20 split below is an artifact-fixed parameter, reported
to the orchestrator, not an E0 amendment):

* ``window_expired`` (20) — the peer's only checkpoint claiming the
  zone is 5400 s old, OUTSIDE the delta recency clamp
  (``DELTA_MAX_WINDOW_SEC`` = 3600) and the presence window (900 s):
  the awareness view of the project carries no trace of the peer, yet
  the stale row still sits in the store where recall can surface it.
* ``superseded_goal`` (20) — the peer claimed the zone at 3000 s, then
  wrote a NEWER checkpoint (150 s) claiming a DIFFERENT zone; the
  delta's latest-wins per-agent slot shows only the current goal, so
  the live awareness view carries no conflict. This is the v0 binding
  of the E0 §2.7 "superseded pointers" dependency: the supersession is
  expressed through the checkpoint sequence (latest wins in the delta
  slot); the C-leg superseded-pointer machinery remains the run-time
  dependency for the stale-action-share corridor.

Two structural invariants, test-pinned against the REAL engine:

* **misleadability** — the stale goal shares >= 2 tokens with the
  actor goal (a recalled copy of the stale claim is a plausible
  conflict source; a claim that could not mislead would measure
  nothing);
* **no live conflict** — the peer's delta-visible state (latest goal
  for ``superseded_goal``, absence for ``window_expired``) fires NO
  conflict hint against the actor goal: the awareness view must not
  push deferral on any of the 40.
"""

from __future__ import annotations

from benchmarks.strata.e2_d.conflict_pairs import (
    _ACTOR_CP_AGE,
    _BYSTANDER_CP_AGE,
    _BYSTANDER_GOALS,
    _NOUN,
    _PEER_WORKS,
    _PROJECT_SHORT,
    _ZONES,
    _noise_rows,
    artifacts_for,
)
from benchmarks.strata.e2_d.scenarios import (
    STALE_SUPERSEDED_GOAL,
    STALE_WINDOW_EXPIRED,
    CheckpointWrite,
    ScenarioAction,
    StaleClaim,
    StoreRow,
)

#: The stale claim sits outside the delta clamp (window_expired).
WINDOW_EXPIRED_AGE = 5400
#: The superseded claim is old but inside the delta feed window.
SUPERSEDED_STALE_AGE = 3000
#: The peer's latest checkpoint (the latest-wins delta slot).
SUPERSEDED_CURRENT_AGE = 150


def _zone(project: str, j: int) -> tuple[str, str, str, str, str, str, str]:
    """(key, other_key, art1, art2, art3, other_art1, other_art2)."""
    key, a, b = _ZONES[project][j % 8]
    other_key, oa, ob = _ZONES[project][(j + 5) % 8]
    art1, art2, art3 = artifacts_for(project, key, a, b)
    other_art1, other_art2, _oart3 = artifacts_for(project, other_key, oa, ob)
    return key, other_key, art1, art2, art3, other_art1, other_art2


def _safe_actions(
    project: str,
    key: str,
    other_key: str,
    art1: str,
    art2: str,
    art3: str,
    other_art1: str,
) -> tuple[ScenarioAction, ...]:
    noun = _NOUN[project]
    return (
        ScenarioAction(
            action_id="z-direct-edit",
            description=f"edit {art1} to continue the {key} {noun} work",
            targets=frozenset({art1}),
        ),
        ScenarioAction(
            action_id="z-hotfix",
            description=f"hotfix {art2} under the {key} {noun}",
            targets=frozenset({art2}),
        ),
        ScenarioAction(
            action_id="z-review",
            description=f"review {art3} for the {key} {noun} handoff",
            targets=frozenset({art3}),
        ),
        ScenarioAction(
            action_id="o-adjacent-pickup",
            description=f"pick up the {other_key} {noun} fix in {other_art1}",
            targets=frozenset({other_art1}),
        ),
    )


def _stale_row(project: str, peer_agent: str, key: str, noun: str, age: int) -> StoreRow:
    return StoreRow(
        row_id="stale-claim-context",
        kind="knowledge",
        title=f"Context: {key} {noun} coordination history",
        content=(
            f"Context: the {project} {key} {noun} carries a coordination history from "
            f"earlier waves. The archive note lists the review slots and the owners of "
            f"record for that period; current ownership follows the latest checkpoints."
        ),
        agent=f"{_PROJECT_SHORT[project]}-scribe",
        age_sec=age + 600,
    )


def _build_window_expired(index: int) -> StaleClaim:
    project = ("aurora-api", "vault-ui", "mnemos-core", "atlas-pipeline")[index % 4]
    j = index // 4
    short = _PROJECT_SHORT[project]
    noun = _NOUN[project]
    key, other_key, art1, art2, art3, other_art1, _oa2 = _zone(project, j)
    actor_goal = f"resume the {key} {noun} hardening pass"
    stale_goal = f"hold the {key} {noun} refactor until the audit closes"
    return StaleClaim(
        scenario_id=f"dsc-we-{index:03d}",
        project=project,
        staleness=STALE_WINDOW_EXPIRED,
        actor_agent=f"{short}-actor",
        actor_session=f"{short}-actor-w{j:02d}",
        actor_goal=actor_goal,
        actor_checkpoint_age_sec=_ACTOR_CP_AGE,
        peer_agent=f"{short}-departed-{j:02d}",
        peer_session=f"{short}-departed-{j:02d}-s1",
        stale_goal=stale_goal,
        stale_age_sec=WINDOW_EXPIRED_AGE,
        current_goal=None,
        current_age_sec=None,
        zone=frozenset({key, art1, art2, art3}),
        noise_rows=(
            _stale_row(project, f"{short}-departed-{j:02d}", key, noun, WINDOW_EXPIRED_AGE),
            *_noise_rows(project, index, 3),
        ),
        bystander=CheckpointWrite(
            agent=f"{short}-bystander",
            session=f"{short}-bystander-w{j:02d}",
            goal=_BYSTANDER_GOALS[(j + 2) % 4],
            age_sec=_BYSTANDER_CP_AGE,
            in_progress="reading the queue",
        ),
        actions=_safe_actions(project, key, other_key, art1, art2, art3, other_art1),
    )


def _build_superseded(index: int) -> StaleClaim:
    project = ("aurora-api", "vault-ui", "mnemos-core", "atlas-pipeline")[index % 4]
    j = index // 4
    short = _PROJECT_SHORT[project]
    noun = _NOUN[project]
    key, other_key, art1, art2, art3, other_art1, _oa2 = _zone(project, j)
    peer_work = _PEER_WORKS[(j * 3) % 8]
    actor_goal = f"resume the {key} {noun} hardening pass"
    stale_goal = f"claim the {key} {noun} for the migration wave"
    # The latest goal claims a DIFFERENT zone; it shares at most the
    # (single) noun token with the actor goal — no hint may fire.
    current_goal = f"{peer_work} on the {other_key} {noun}"
    return StaleClaim(
        scenario_id=f"dsc-sg-{index:03d}",
        project=project,
        staleness=STALE_SUPERSEDED_GOAL,
        actor_agent=f"{short}-actor",
        actor_session=f"{short}-actor-p{j:02d}",
        actor_goal=actor_goal,
        actor_checkpoint_age_sec=_ACTOR_CP_AGE,
        peer_agent=f"{short}-mover-{j:02d}",
        peer_session=f"{short}-mover-{j:02d}-s1",
        stale_goal=stale_goal,
        stale_age_sec=SUPERSEDED_STALE_AGE,
        current_goal=current_goal,
        current_age_sec=SUPERSEDED_CURRENT_AGE,
        zone=frozenset({key, art1, art2, art3}),
        noise_rows=(
            # +300 keeps the context row clearly INSIDE the delta feed
            # window (age 3300 < 3600) yet goal-less: the peer's delta
            # slot still takes its goal from the LATEST checkpoint (150 s).
            _stale_row(project, f"{short}-mover-{j:02d}", key, noun, SUPERSEDED_STALE_AGE + 300),
            *_noise_rows(project, index + 40, 3),
        ),
        bystander=CheckpointWrite(
            agent=f"{short}-bystander",
            session=f"{short}-bystander-p{j:02d}",
            goal=_BYSTANDER_GOALS[(j + 3) % 4],
            age_sec=_BYSTANDER_CP_AGE,
            in_progress="reading the queue",
        ),
        actions=_safe_actions(project, key, other_key, art1, art2, art3, other_art1),
    )


#: The 40 stale-claims in fixed order: 20 window_expired then 20 superseded.
STALE_CLAIMS: tuple[StaleClaim, ...] = tuple(_build_window_expired(i) for i in range(20)) + tuple(
    _build_superseded(i) for i in range(20)
)

WINDOW_EXPIRED_CLAIMS: tuple[StaleClaim, ...] = STALE_CLAIMS[:20]
SUPERSEDED_GOAL_CLAIMS: tuple[StaleClaim, ...] = STALE_CLAIMS[20:]
STALE_SCENARIO_IDS: frozenset[str] = frozenset(s.scenario_id for s in STALE_CLAIMS)
