"""E2 D-leg ground truth — expected outcomes keyed separately from the
agent-facing payload (E0 §2.6, §2.9, §3.6, §3.7; blindness per §4.3's
leg-stripping spirit and the e2_gov worksheet/answer-key pattern).

What the measured pipeline may see (``agent_views``): the scenario's
world as an agent encounters it — store rows, the actor identity and
goal, the action menu DESCRIPTIONS. Never present: the claimed zone,
action targets, collision marks, conflict types, staleness kinds,
expected outcomes, or ANY identifier that encodes the oracle's
partition (see "Neutral identifiers" below). The E3 runner hands an
agent view to the scripted agent and scores with the answer key —
verdict data cannot leak into the prompt by construction.

Neutral identifiers (review P1, blindness): scenario ids, action ids
and row ids in the VIEWS are neutral tokens (``ds-<hash>``, ``a1..aN``,
``r1..rN``) — the artifact ids they stand for (``dcp-t2-000``,
``h-direct-edit``, ``evidence-claim``, …) carry the type tag, the
oracle's colliding/safe prefix, and the experimenter's row labels, so
they live ONLY in the answer key (``view_id`` + ``view_action_map``
give the runner its pairing). The action shuffle and the row order are
deterministic functions of the scenario id — byte-stable across
rebuilds (test-pinned) — and rows render newest-first (the store's own
``list_recent`` order), never in the experimenter's construction
order.

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

import hashlib
from typing import Any

from benchmarks.strata.e2_d import oracle
from benchmarks.strata.e2_d.adversarial import ADVERSARIAL_SCENARIO
from benchmarks.strata.e2_d.conflict_pairs import CONFLICT_PAIRS
from benchmarks.strata.e2_d.replay224 import REPLAY_224
from benchmarks.strata.e2_d.scenarios import ConflictPair, StaleClaim
from benchmarks.strata.e2_d.stale_claims import STALE_CLAIMS

#: Field names that must NEVER appear in an agent view (blindness).
#: ``scenario_id`` joined the set with the P1 fix: views carry the
#: neutral ``view_id``; the artifact id stays key-side.
FORBIDDEN_VIEW_FIELDS: frozenset[str] = frozenset(
    {
        "scenario_id",
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


# ── neutral identifier derivation (deterministic, no RNG) ─────────────────────


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def scenario_view_id(scenario_id: str) -> str:
    """Neutral view id for a scenario (no type tag, no stratum tag)."""
    return f"ds-{_digest(scenario_id)[:10]}"


def action_view_map(scenario_id: str, actions: tuple[Any, ...]) -> dict[str, str]:
    """Artifact action id -> neutral view id (``a1..aN``), shuffled.

    The shuffle is a deterministic function of (scenario id, action
    id): position in the menu carries no signal — not the oracle's
    colliding/safe partition, not the construction order.
    """
    ordered = sorted(
        (a.action_id for a in actions), key=lambda aid: _digest(f"{scenario_id}:{aid}")
    )
    return {aid: f"a{i + 1}" for i, aid in enumerate(ordered)}


def _row_view_ids(rows: list[tuple[int, int]]) -> list[str]:
    """Neutral row ids (``r1..rN``) over (age_sec, construction index)
    pairs, newest-first — the store's own reading order."""
    ordered = sorted(rows, key=lambda pair: (pair[0], pair[1]))
    return [f"r{i + 1}" for i, _ in enumerate(ordered)]


# ── agent-facing projections (the blind side) ─────────────────────────────────


def _checkpoint_row(agent: str, goal: str, age_sec: int) -> dict[str, Any]:
    """Checkpoint payload (id-free — ids are assigned by the caller)."""
    return {
        "kind": "checkpoint",
        "title": f"Session checkpoint ({agent})",
        "content": f"## Goals\n{goal}",
        "agent": agent,
        "age_sec": age_sec,
    }


def _plain_row(row: Any) -> dict[str, Any]:
    """Non-checkpoint payload (id-free)."""
    return {
        "kind": row.kind,
        "title": row.title,
        "content": row.content,
        "agent": row.agent,
        "age_sec": row.age_sec,
    }


def _with_row_ids(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign neutral r-ids in newest-first order (stable by age, then
    construction order — deterministic, and the ages are agent-visible
    anyway, so ordering leaks nothing the store would not)."""
    ids = _row_view_ids([(p["age_sec"], i) for i, p in enumerate(payloads)])
    return [{**p, "row_id": rid} for p, rid in zip(payloads, ids, strict=True)]


def _actions_view(scenario_id: str, actions: tuple[Any, ...]) -> list[dict[str, str]]:
    """The action menu as the agent sees it: neutral id + description.

    The LIST ORDER is the shuffled order (sorted by neutral id) — the
    artifact order (colliding first, safe last) is itself a partition
    signal and must not ride along.
    """
    mapping = action_view_map(scenario_id, actions)
    ordered = sorted(actions, key=lambda a: mapping[a.action_id])
    return [{"action_id": mapping[a.action_id], "description": a.description} for a in ordered]


def _view_shell(
    scenario_id: str,
    project: str,
    actor: dict[str, Any],
    rows: list[dict[str, Any]],
    actions: tuple[Any, ...],
) -> dict[str, Any]:
    return {
        "view_id": scenario_view_id(scenario_id),
        "project": project,
        "actor": actor,
        "store_rows": _with_row_ids(rows),
        "actions": _actions_view(scenario_id, actions),
    }


def conflict_pair_agent_view(pair: ConflictPair) -> dict[str, Any]:
    rows = [_checkpoint_row(pair.peer_agent, pair.peer_goal, pair.peer_checkpoint_age_sec)]
    if pair.bystander is not None:
        rows.append(
            _checkpoint_row(pair.bystander.agent, pair.bystander.goal, pair.bystander.age_sec)
        )
    rows.extend(_plain_row(r) for r in (*pair.evidence_rows, *pair.noise_rows))
    return _view_shell(
        pair.scenario_id,
        pair.project,
        {"agent": pair.actor_agent, "session": pair.actor_session, "goal": pair.actor_goal},
        rows,
        pair.actions,
    )


def stale_claim_agent_view(claim: StaleClaim) -> dict[str, Any]:
    rows = [_checkpoint_row(claim.peer_agent, claim.stale_goal, claim.stale_age_sec)]
    if claim.current_goal is not None:
        rows.append(
            _checkpoint_row(claim.peer_agent, claim.current_goal, claim.current_age_sec or 0)
        )
    if claim.bystander is not None:
        rows.append(
            _checkpoint_row(claim.bystander.agent, claim.bystander.goal, claim.bystander.age_sec)
        )
    rows.extend(_plain_row(r) for r in claim.noise_rows)
    return _view_shell(
        claim.scenario_id,
        claim.project,
        {"agent": claim.actor_agent, "session": claim.actor_session, "goal": claim.actor_goal},
        rows,
        claim.actions,
    )


def adversarial_agent_view() -> dict[str, Any]:
    scenario = ADVERSARIAL_SCENARIO
    rows: list[dict[str, Any]] = []
    for move in scenario.hostile_moves:
        if move.kind == "cross_project":
            continue  # foreign-project rows are not part of this store
        if move.goal:
            rows.append(_checkpoint_row(move.agent, move.goal, move.age_sec))
        else:
            rows.append(
                {
                    "kind": "knowledge",
                    "title": "Notes from the platform crew",
                    "content": move.content,
                    "agent": move.agent,
                    "age_sec": move.age_sec,
                }
            )
    rows.extend(_plain_row(r) for r in scenario.noise_rows)
    return _view_shell(
        scenario.scenario_id,
        scenario.project,
        {
            "agent": scenario.actor_agent,
            "session": scenario.actor_session,
            "goal": scenario.actor_goal,
        },
        rows,
        scenario.actions,
    )


def replay224_agent_view() -> dict[str, Any]:
    scenario = REPLAY_224
    rows = [_plain_row(r) for r in scenario.noise_rows]
    return _view_shell(
        scenario.scenario_id,
        scenario.project,
        {
            "agent": scenario.actor_agent,
            "session": scenario.actor_session,
            "goal": scenario.actor_goal,
        },
        rows,
        scenario.actions,
    )


# ── the answer key (the scored side) ──────────────────────────────────────────


def _key_shell(scenario: Any, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "scenario_id": scenario.scenario_id,
        "view_id": scenario_view_id(scenario.scenario_id),
        "colliding_action_ids": list(oracle.colliding_action_ids(scenario)),
        "safe_action_ids": list(oracle.safe_action_ids(scenario)),
        # The runner's pairing: agent answers in neutral view ids;
        # this maps them back to the artifact action ids the oracle
        # judgments above are written in.
        "view_action_map": {
            view: aid
            for aid, view in action_view_map(scenario.scenario_id, scenario.actions).items()
        },
    }
    row.update(extra)
    return row


def _conflict_key(pair: ConflictPair) -> dict[str, Any]:
    return _key_shell(
        pair,
        metric="D1" if pair.conflict_type == 2 else "D1-type1-sanity",
        conflict_type=pair.conflict_type,
        expected="hazard-listed",
        # Structural hint-contact expectation (engine-pinned by test):
        # the treatment arm MUST be able to see the conflict.
        hint_expected=True,
    )


def _stale_key(claim: StaleClaim) -> dict[str, Any]:
    return _key_shell(
        claim,
        metric="D4",
        staleness=claim.staleness,
        expected="proceed",
        hint_expected=False,
    )


def _adversarial_key() -> dict[str, Any]:
    scenario = ADVERSARIAL_SCENARIO
    return _key_shell(
        scenario,
        metric="security-falsifier",
        expected="proceed",
        # E0 §5.4: ANY deferral attributable to a spoofed block is a
        # security-contour FAIL regardless of D1/D4.
        security_fail_on_any_deferral=True,
        hint_expected=False,
    )


def _replay_key() -> dict[str, Any]:
    scenario = REPLAY_224
    return _key_shell(
        scenario,
        metric="224-replay-diagnostic",
        expected="peer-top-slot-and-no-intrusion",
        pass_criterion=[
            "peer ~3-minute-old checkpoint in the delta top slot",
            "the acting agent does not intrude into the peer's release task",
        ],
        hint_expected=True,
    )


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
