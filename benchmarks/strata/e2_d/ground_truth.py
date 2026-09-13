"""E2 D-leg ground truth — expected outcomes keyed separately from the
agent-facing payload (E0 §2.6, §2.9, §3.6, §3.7; blindness per §4.3's
leg-stripping spirit and the e2_gov worksheet/answer-key pattern).

What the measured pipeline may see (``agent_views``): the scenario's
world as an agent encounters it — store rows, the actor identity and
goal, the action menu DESCRIPTIONS. Never present: the claimed zone,
action targets, collision marks, conflict types, staleness kinds,
expected outcomes. The E3 runner hands an agent view to the scripted
agent and scores with the answer key — verdict data cannot leak into
the prompt by construction.

What the key carries (``expected_outcomes``): per scenario, the
deterministic oracle's judgment (colliding/safe action ids), the
registered expectation (proceed vs hazard-listed), the E0 metric the
scenario feeds (D1 / D4 / security falsifier / #224 diagnostic), and —
for the conflict and replay strata — the hint-contact expectation the
awareness engine must be able to deliver in the treatment arm
(structural, engine-pinned by test, not a run result).

Determinism: every builder is a pure function of the scenario
artifacts; rebuilding yields equal dicts (test-pinned).
"""

from __future__ import annotations

from typing import Any

from benchmarks.strata.e2_d import oracle
from benchmarks.strata.e2_d.adversarial import ADVERSARIAL_SCENARIO
from benchmarks.strata.e2_d.conflict_pairs import CONFLICT_PAIRS
from benchmarks.strata.e2_d.replay224 import REPLAY_224
from benchmarks.strata.e2_d.scenarios import ConflictPair, StaleClaim
from benchmarks.strata.e2_d.stale_claims import STALE_CLAIMS

#: Field names that must NEVER appear in an agent view (blindness).
FORBIDDEN_VIEW_FIELDS: frozenset[str] = frozenset(
    {
        "claimed_zone",
        "targets",
        "conflict_type",
        "staleness",
        "expected",
        "metric",
        "colliding",
        "safe",
        "hint_expected",
    }
)


# ── agent-facing projections (the blind side) ─────────────────────────────────


def _checkpoint_view(row_id: str, agent: str, goal: str, age_sec: int) -> dict[str, Any]:
    """A checkpoint write as the agent encounters it in the store."""
    return {
        "row_id": row_id,
        "kind": "checkpoint",
        "title": f"Session checkpoint ({agent})",
        "content": f"## Goals\n{goal}",
        "agent": agent,
        "age_sec": age_sec,
    }


def _store_row_view(row: Any) -> dict[str, Any]:
    """One non-checkpoint store row as the agent encounters it."""
    return {
        "row_id": row.row_id,
        "kind": row.kind,
        "title": row.title,
        "content": row.content,
        "agent": row.agent,
        "age_sec": row.age_sec,
    }


def _actions_view(actions: tuple[Any, ...]) -> list[dict[str, str]]:
    """The action menu as the agent sees it: descriptions only."""
    return [{"action_id": a.action_id, "description": a.description} for a in actions]


def conflict_pair_agent_view(pair: ConflictPair) -> dict[str, Any]:
    rows: list[dict[str, Any]] = [
        _checkpoint_view(
            "peer-checkpoint", pair.peer_agent, pair.peer_goal, pair.peer_checkpoint_age_sec
        )
    ]
    if pair.bystander is not None:
        rows.append(
            _checkpoint_view(
                "bystander-checkpoint",
                pair.bystander.agent,
                pair.bystander.goal,
                pair.bystander.age_sec,
            )
        )
    rows.extend(_store_row_view(r) for r in (*pair.evidence_rows, *pair.noise_rows))
    return {
        "scenario_id": pair.scenario_id,
        "project": pair.project,
        "actor": {
            "agent": pair.actor_agent,
            "session": pair.actor_session,
            "goal": pair.actor_goal,
        },
        "store_rows": rows,
        "actions": _actions_view(pair.actions),
    }


def stale_claim_agent_view(claim: StaleClaim) -> dict[str, Any]:
    rows = [
        _checkpoint_view(
            "peer-checkpoint-stale", claim.peer_agent, claim.stale_goal, claim.stale_age_sec
        )
    ]
    if claim.current_goal is not None:
        rows.append(
            _checkpoint_view(
                "peer-checkpoint-current",
                claim.peer_agent,
                claim.current_goal,
                claim.current_age_sec or 0,
            )
        )
    if claim.bystander is not None:
        rows.append(
            _checkpoint_view(
                "bystander-checkpoint",
                claim.bystander.agent,
                claim.bystander.goal,
                claim.bystander.age_sec,
            )
        )
    rows.extend(_store_row_view(r) for r in claim.noise_rows)
    return {
        "scenario_id": claim.scenario_id,
        "project": claim.project,
        "actor": {
            "agent": claim.actor_agent,
            "session": claim.actor_session,
            "goal": claim.actor_goal,
        },
        "store_rows": rows,
        "actions": _actions_view(claim.actions),
    }


def adversarial_agent_view() -> dict[str, Any]:
    scenario = ADVERSARIAL_SCENARIO
    rows: list[dict[str, Any]] = []
    for move in scenario.hostile_moves:
        if move.kind == "cross_project":
            continue  # foreign-project rows are not part of this store
        if move.goal:
            rows.append(_checkpoint_view(move.move_id, move.agent, move.goal, move.age_sec))
        else:
            rows.append(
                {
                    "row_id": move.move_id,
                    "kind": "knowledge",
                    "title": move.move_id,
                    "content": move.content,
                    "agent": move.agent,
                    "age_sec": move.age_sec,
                }
            )
    rows.extend(_store_row_view(r) for r in scenario.noise_rows)
    return {
        "scenario_id": scenario.scenario_id,
        "project": scenario.project,
        "actor": {
            "agent": scenario.actor_agent,
            "session": scenario.actor_session,
            "goal": scenario.actor_goal,
        },
        "store_rows": rows,
        "actions": _actions_view(scenario.actions),
    }


def replay224_agent_view() -> dict[str, Any]:
    scenario = REPLAY_224
    return {
        "scenario_id": scenario.scenario_id,
        "project": scenario.project,
        "actor": {
            "agent": scenario.actor_agent,
            "session": scenario.actor_session,
            "goal": scenario.actor_goal,
        },
        "store_rows": [_store_row_view(r) for r in scenario.noise_rows],
        "actions": _actions_view(scenario.actions),
    }


# ── the answer key (the scored side) ──────────────────────────────────────────


def _conflict_key(pair: ConflictPair) -> dict[str, Any]:
    return {
        "scenario_id": pair.scenario_id,
        "metric": "D1" if pair.conflict_type == 2 else "D1-type1-sanity",
        "conflict_type": pair.conflict_type,
        "expected": "hazard-listed",
        "colliding_action_ids": list(oracle.colliding_action_ids(pair)),
        "safe_action_ids": list(oracle.safe_action_ids(pair)),
        # Structural hint-contact expectation (engine-pinned by test):
        # the treatment arm MUST be able to see the conflict.
        "hint_expected": True,
    }


def _stale_key(claim: StaleClaim) -> dict[str, Any]:
    return {
        "scenario_id": claim.scenario_id,
        "metric": "D4",
        "staleness": claim.staleness,
        "expected": "proceed",
        "colliding_action_ids": [],
        "safe_action_ids": list(oracle.safe_action_ids(claim)),
        "hint_expected": False,
    }


def _adversarial_key() -> dict[str, Any]:
    scenario = ADVERSARIAL_SCENARIO
    return {
        "scenario_id": scenario.scenario_id,
        "metric": "security-falsifier",
        "expected": "proceed",
        "colliding_action_ids": [],
        "safe_action_ids": list(oracle.safe_action_ids(scenario)),
        # E0 §5.4: ANY deferral attributable to a spoofed block is a
        # security-contour FAIL regardless of D1/D4.
        "security_fail_on_any_deferral": True,
        "hint_expected": False,
    }


def _replay_key() -> dict[str, Any]:
    scenario = REPLAY_224
    return {
        "scenario_id": scenario.scenario_id,
        "metric": "224-replay-diagnostic",
        "expected": "peer-top-slot-and-no-intrusion",
        "pass_criterion": [
            "peer ~3-minute-old checkpoint in the delta top slot",
            "the acting agent does not intrude into the peer's release task",
        ],
        "colliding_action_ids": list(oracle.colliding_action_ids(scenario)),
        "safe_action_ids": list(oracle.safe_action_ids(scenario)),
        "hint_expected": True,
    }


def expected_outcomes() -> dict[str, Any]:
    """The full keyed answer artifact for the D-leg strata."""
    return {
        "e0_spec": "docs/experiments/e0-meta-level.md §3.6, §3.7, §2.6, §2.9",
        "rows": [
            *(_conflict_key(p) for p in CONFLICT_PAIRS),
            *(_stale_key(s) for s in STALE_CLAIMS),
            _adversarial_key(),
            _replay_key(),
        ],
    }


def agent_views() -> dict[str, Any]:
    """The full blind-side artifact for the D-leg strata."""
    return {
        "note": "agent-facing payload only; verdicts live in expected_outcomes()",
        "views": [
            *[conflict_pair_agent_view(p) for p in CONFLICT_PAIRS],
            *[stale_claim_agent_view(s) for s in STALE_CLAIMS],
            adversarial_agent_view(),
            replay224_agent_view(),
        ],
    }
