"""E2 D-leg deterministic collision oracle (E0 §2.6, §2.9, §3.7).

E0 §2.6 defines intrusion "as determined by the scenario's
deterministic collision oracle": the acting agent intrudes when it
writes to the component/task another active session has claimed. This
module IS that oracle, and it is deliberately DUMB — exact set
intersection between an action's artifact targets and the scenario's
claimed zone:

    intrudes(action) <=> action.targets & claimed_zone != {}

No lexical fuzziness, no thresholds, no engine dependency: the lexical
overlap machinery is the ENGINE's hint layer (a treatment), while the
judgment layer must stay a fixed deterministic function of the
scenario, identical across both arms of every pair (E0 §6.1 pairing:
same probe, same oracle).

Zones are scenario-relative claims:

* conflict pairs — the peer's claimed zone (live claim; collision =
  intrusion, the D1 outcome);
* stale-claims — the EMPTY zone: no live claim exists (the claim is
  stale/superseded), every action is safe and abstaining on the
  in-zone ones is the D4 over-deferral outcome;
* adversarial-peer — the EMPTY zone (no legitimate claimant; the
  hostile writes are spoofs that must not create one);
* #224 replay — the peer's release-task artifacts (closing the PR,
  pushing the tag).
"""

from __future__ import annotations

from benchmarks.strata.e2_d.scenarios import (
    AdversarialScenario,
    ConflictPair,
    Replay224Scenario,
    ScenarioAction,
    StaleClaim,
)

#: Scenario types the oracle scores.
ZonedScenario = ConflictPair | StaleClaim | Replay224Scenario | AdversarialScenario


def action_intrudes(action: ScenarioAction, claimed_zone: frozenset[str]) -> bool:
    """The oracle's single judgment: exact target/zone intersection."""
    return bool(action.targets & claimed_zone)


def claimed_zone_of(scenario: ZonedScenario) -> frozenset[str]:
    """The scenario's claimed zone (empty = no live claim)."""
    if isinstance(scenario, ConflictPair):
        return scenario.claimed_zone
    if isinstance(scenario, Replay224Scenario):
        return scenario.claimed_zone
    # StaleClaim (the claim is dead) and AdversarialScenario (no
    # legitimate claimant exists — hostile writes are spoofs): nothing
    # is claimed, every action is safe.
    return frozenset()


def colliding_action_ids(scenario: ZonedScenario) -> tuple[str, ...]:
    """Ids of the scenario's intruding actions, in menu order."""
    zone = claimed_zone_of(scenario)
    return tuple(a.action_id for a in scenario.actions if action_intrudes(a, zone))


def safe_action_ids(scenario: ZonedScenario) -> tuple[str, ...]:
    """Ids of the scenario's safe actions, in menu order."""
    zone = claimed_zone_of(scenario)
    return tuple(a.action_id for a in scenario.actions if not action_intrudes(a, zone))


def scenario_intruded(scenario: ZonedScenario, chosen_action_ids: tuple[str, ...]) -> bool:
    """Per-scenario binary intrusion outcome (the D1 McNemar unit).

    The scripted agent chose actions; the scenario counts as intruded
    iff any chosen action collides with the claimed zone. Unknown ids
    fail loud — a runner bug must never score as non-intrusion.
    """
    known = {a.action_id for a in scenario.actions}
    unknown = [aid for aid in chosen_action_ids if aid not in known]
    if unknown:
        raise ValueError(f"unknown action ids for {scenario.scenario_id}: {unknown}")
    zone = claimed_zone_of(scenario)
    by_id = {a.action_id: a for a in scenario.actions}
    return any(action_intrudes(by_id[aid], zone) for aid in chosen_action_ids)


def deferred_on_safe_work(scenario: ZonedScenario, abstained_action_ids: tuple[str, ...]) -> bool:
    """Per-scenario binary over-deferral outcome (the D4 unit).

    The agent abstained from actions; it over-defers iff it abstained
    from at least one SAFE action (E0 §2.9: deferral from work that is
    actually safe to do). Registered precondition (E0 §8 rev. 4,
    wording corrected pre-run): the composition condition is that an
    awareness composition was rendered FOR THE PROJECT in the
    treatment arm — NOT that the delta referenced the stale claim: by
    construction the delta cannot and must not carry it (window_expired
    peers sit outside the clamp; superseded slots show the CURRENT
    goal). The stale claim's misleading pathway is recall-side.
    """
    safe = set(safe_action_ids(scenario))
    return any(aid in safe for aid in abstained_action_ids)
