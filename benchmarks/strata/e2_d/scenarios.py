"""E2 awareness strata — the scenario artifact model (E0 §3.6, §3.7).

The D-leg strata are SCENARIOS, not retrieval queries: each artifact
describes a small deterministic world (two agents' goals overlap, a
claim went stale, a hostile peer spoofs presence) that the future E3
runner materializes into a fresh store and executes against the REAL
awareness engine (``mnemos.awareness``). Nothing here imports the
engine — the binding is by data shape:

* every goal is a single ASCII line <= ``GOAL_TITLE_MAX_CHARS`` — the
  engine's tokenizer is ASCII-blind by construction (E0 §8 rev. 2:
  ``[a-z0-9][a-z0-9_.\\-]+`` over lowercased text, stopwords dropped,
  hint fires at >= ``CONFLICT_HINT_MIN_SHARED_TOKENS`` = 2 distinct
  shared tokens), so a non-ASCII goal would yield an empty token set
  and never fire a hint — D-scenario goals MUST be ASCII;
* every checkpoint write mirrors the #251 server channel shape: the
  Goals first line is the goal title, identity rides the
  ``checkpoint_agent`` / ``checkpoint_session`` stamps, ``age_sec`` is
  the offset before the scenario's frozen ``now`` (the runner
  materializes ``created_at = run_now - age_sec``; see
  :mod:`benchmarks.strata.e2_d.materialize`);
* every scenario carries a candidate action menu with explicit
  ``targets`` (artifact stems) and a claimed zone; the deterministic
  collision oracle (E0 §2.6) is exact set intersection —
  ``action.targets & claimed_zone`` — no lexical fuzziness in the
  JUDGMENT layer (lexical overlap is the engine's hint layer, not the
  oracle's).

Ground-truth discipline (blindness, e2_gov pattern): the agent-facing
projection of a scenario (store rows + goals + action descriptions)
never carries verdict fields; expected outcomes live in the separate
keyed artifact (:mod:`benchmarks.strata.e2_d.ground_truth`).
"""

from __future__ import annotations

from dataclasses import dataclass

#: Conflict kinds per E0 §2.6 / §2.10: type 1 = file-visible (the claim
#: is discoverable in non-checkpoint store rows), type 2 = intent-only
#: (the claim lives EXCLUSIVELY in the peer's checkpoint goal).
TYPE_1_FILE_VISIBLE = 1
TYPE_2_INTENT_CONFLICT = 2

#: Stale-claim mechanisms (E0 §3.6 "stale/superseded presence or goal
#: claims"; E0 §2.7 stale-action share). The E0-unspecified mechanism
#: split is fixed here and reported: window_expired = the peer's last
#: write is older than the delta recency clamp; superseded_goal = the
#: peer's LATEST checkpoint claims a different zone (latest-wins slot).
STALE_WINDOW_EXPIRED = "window_expired"
STALE_SUPERSEDED_GOAL = "superseded_goal"


@dataclass(frozen=True)
class ScenarioAction:
    """One candidate action of the scripted agent (the oracle's unit).

    ``targets`` are artifact stems (paths / task handles). In
    agent-facing views the action appears under a NEUTRAL per-scenario
    id (``a1..aN``, shuffled — ``ground_truth.action_view_map``) with
    its ``description``; the artifact ``action_id`` and ``targets``
    stay key-side so neither the judgment layer nor id-prefix classes
    ever leak into the prompt (review P1).
    """

    action_id: str
    description: str
    targets: frozenset[str]


@dataclass(frozen=True)
class CheckpointWrite:
    """A #251-channel checkpoint write at a relative age (pure data)."""

    agent: str
    session: str
    goal: str
    age_sec: int
    in_progress: str = ""


@dataclass(frozen=True)
class StoreRow:
    """A non-checkpoint row of the scenario store (pure data)."""

    row_id: str
    kind: str  # "decision" | "knowledge" | "learning"
    title: str
    content: str
    agent: str
    age_sec: int


@dataclass(frozen=True)
class ConflictPair:
    """E0 §3.6 conflict pair: actor and peer goals overlap on a zone.

    Type 2: the peer's claim on ``claimed_zone`` exists ONLY in the
    peer checkpoint goal (``evidence_rows`` is empty of zone tokens).
    Type 1: additionally a non-checkpoint row carries the claim, so a
    competent agent reading current files already avoids the zone
    (E0 §2.10 sanity floor).
    """

    scenario_id: str
    project: str
    conflict_type: int
    actor_agent: str
    actor_session: str
    actor_goal: str
    actor_checkpoint_age_sec: int
    peer_agent: str
    peer_session: str
    peer_goal: str
    peer_checkpoint_age_sec: int
    claimed_zone: frozenset[str]
    evidence_rows: tuple[StoreRow, ...]
    noise_rows: tuple[StoreRow, ...]
    bystander: CheckpointWrite | None
    actions: tuple[ScenarioAction, ...]


@dataclass(frozen=True)
class StaleClaim:
    """E0 §3.6 stale-claim: a presence/goal claim that is stale or
    superseded; the work in ``zone`` is actually SAFE (E0 §2.9 D4).

    ``stale_goal`` is the misleading claim (it must lexically overlap
    the actor goal so recall-surfaced copies can mislead). For
    superseded_goal the peer's ``current_goal`` is the latest-wins
    checkpoint and must NOT re-claim the zone.
    """

    scenario_id: str
    project: str
    staleness: str
    actor_agent: str
    actor_session: str
    actor_goal: str
    actor_checkpoint_age_sec: int
    peer_agent: str
    peer_session: str
    stale_goal: str
    stale_age_sec: int
    current_goal: str | None
    current_age_sec: int | None
    zone: frozenset[str]
    noise_rows: tuple[StoreRow, ...]
    bystander: CheckpointWrite | None
    actions: tuple[ScenarioAction, ...]


@dataclass(frozen=True)
class CanaryFact:
    """E0 §3.6 noisy canary: a noisy-but-legitimate write-boundary fact.

    ``channel`` selects the boundary: "add" (generic create) or
    "checkpoint" (the #251 save_checkpoint channel — ``goals`` /
    ``completed`` / ``in_progress`` are the channel fields). The
    registered corridor is false-drop <= 0.01; this stratum pins
    false-drop = 0 at the CURRENT boundary by design (every canary is
    detector-clean) and by test (the real detectors find nothing).
    """

    canary_id: str
    channel: str
    noise_class: str
    title: str
    content: str
    agent: str
    project: str
    goals: str = ""
    completed: str = ""
    in_progress: str = ""


@dataclass(frozen=True)
class HostileMove:
    """One scripted hostile write of the adversarial-peer scenario.

    Kinds: "spoofed_presence" (a real-channel checkpoint asserting a
    false ownership claim — renders under the [unverified] frame),
    "policy_smuggle" (an ``applyTo:`` marker inside a goal — stripped
    at render), "forged_stamp" (client metadata forging the #251
    server stamps through the GENERIC add path — stripped server-side),
    "cross_project" (a hostile checkpoint in a DIFFERENT project —
    project scoping must keep it out of this project's delta).
    """

    move_id: str
    kind: str
    agent: str
    session: str
    goal: str
    age_sec: int
    in_progress: str = ""
    content: str = ""
    forged_metadata: dict[str, str] | None = None
    foreign_project: str | None = None


@dataclass(frozen=True)
class AdversarialScenario:
    """E0 §3.6 adversarial-peer: spoofed presence must not move the
    agent (R3 red line; any deferral attributable to a spoofed block
    is a security-contour FAIL per E0 §5.4)."""

    scenario_id: str
    project: str
    actor_agent: str
    actor_session: str
    actor_goal: str
    actor_checkpoint_age_sec: int
    hostile_moves: tuple[HostileMove, ...]
    noise_rows: tuple[StoreRow, ...]
    actions: tuple[ScenarioAction, ...]


@dataclass(frozen=True)
class Replay224Scenario:
    """E0 §3.7 — the PR #224 incident as a permanent controlled scenario.

    Parallel sessions over one project, release in flight, the peer's
    checkpoint ~3 minutes old (``peer_checkpoint_age_sec`` = 180)
    inside the delta recency window. Pass criterion (diagnostic,
    non-gating): the peer's checkpoint appears in the delta TOP slot
    AND the acting agent does not intrude into the peer's release task.
    """

    scenario_id: str
    project: str
    actor_agent: str
    actor_session: str
    actor_goal: str
    actor_checkpoint_age_sec: int
    peer_agent: str
    peer_session: str
    peer_goal: str
    peer_checkpoint_age_sec: int
    claimed_zone: frozenset[str]
    noise_rows: tuple[StoreRow, ...]
    actions: tuple[ScenarioAction, ...]
    #: Drowning mass (E0 §3.7 "where recall would drown it among 839
    #: checkpoints" — the live-store figure; the scenario reproduces
    #: the CONDITION at controlled scale, see profile fixed_parameters).
    flood_checkpoint_rows: int
    flood_plain_rows: int
