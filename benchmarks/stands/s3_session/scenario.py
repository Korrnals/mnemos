"""S3 scenario generator — the long-lived session as DATA (ADR-0020 BF-3).

The scenario is a fixed list of turn operations produced by a SEEDED
generator (``random.Random(seed)``): same seed → byte-identical op list,
on every machine, with zero wall-clock input. Logical time is the turn
index — nothing in the generated data depends on when the scenario is
built or executed.

Turn operation kinds (one op per turn):

* ``WriteFact``     — record one fact carrying a unique marker token
  (``marker``) plus a verbatim two-word key phrase; every later
  extractability check looks for the MARKER in issued content, so a hit
  is unambiguous attribution, never a lucky lexical overlap;
* ``SearchProbe``   — search for a past fact; ``mode="exact"`` queries
  the verbatim phrase (FTS phrase match), ``mode="paraphrase"`` queries
  the same words reordered plus the topic word (no phrase match — the
  vector leg must carry it), mirroring how a real agent re-asks with
  different wording;
* ``AssembleTurn``  — ``assemble_context`` on a budget (the
  ``pre_llm_call`` shape): ``traffic`` filler, the paired
  ``growth_early`` / ``growth_late`` probes (context-growth-factor) and
  ``task`` steps whose ``required`` markers define sufficiency@task;
* ``RewriteTurn``   — an ``on_context_rewrite``-style event: a context
  block is "compressed", the original goes to memory through the real
  rewrite event (ADR-0018); blocks exceed ``ccr.min_size_chars`` so a
  marker is always minted (fixture-integrity guard at run time);
* ``CheckpointTurn``— checkpoint / restore: a ``mnemos:checkpoint``
  memory is saved (``mnemos_save_context`` semantics), then a NEW
  session restores via ``recall_context``; the op carries the audit
  markers of every fact so far — the checkpoint-return-integrity
  invariant probes them after the round-trip.

Scheduled (non-random) positions keep the metric families fed at any
``turns``: anchors at the head (growth probes need a stable query
target from turn 0), checkpoints / tasks / drift windows / growth probes
at fixed fractions. Free turns are filled by seeded weighted choice.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

# ── Fixed vocabularies (the synthetic session's "world") ──────────────────────

_ADJ: tuple[str, ...] = (
    "amber",
    "basalt",
    "copper",
    "dusky",
    "ebony",
    "fallow",
    "gilded",
    "hazel",
    "indigo",
    "jade",
    "khaki",
    "lavender",
    "mahogany",
    "nickel",
    "olive",
    "opaline",
    "plum",
    "quartz",
    "russet",
    "sable",
    "tawny",
    "umber",
    "verdant",
    "walnut",
    "yarrow",
    "alabaster",
    "bistre",
    "cerulean",
    "denim",
    "ecru",
    "fuchsia",
    "garnet",
    "heliotrope",
    "iris",
    "jet",
    "kobalt",
    "lilac",
    "madder",
    "niobium",
    "onyx",
)

_NOUN: tuple[str, ...] = (
    "ledger",
    "beacon",
    "harbor",
    "lantern",
    "cistern",
    "trestle",
    "palisade",
    "quay",
    "viaduct",
    "granary",
    "foundry",
    "kiln",
    "loom",
    "millrace",
    "paddock",
    "relay",
    "silo",
    "turntable",
    "windlass",
    "archive",
    "ballast",
    "compass",
    "drydock",
    "flume",
    "gantry",
    "hoist",
    "junction",
    "keystone",
    "masthead",
    "odometer",
    "parapet",
    "rostrum",
    "stanchion",
    "trellis",
    "vantage",
    "wayline",
    "yawl",
    "zeppelin",
    "aqueduct",
    "bore",
)

#: Task-cluster topics — the shared word a task's required facts are
#: discoverable by (the task query IS the topic token).
_TOPICS: tuple[str, ...] = (
    "quota",
    "rollover",
    "throttling",
    "backfill",
    "skew",
    "gossip",
    "sharding",
    "compaction",
    "retention",
    "sampling",
    "shedding",
    "warming",
    "mirroring",
    "brokerage",
    "sequencing",
    "attractor",
)

#: Reserved topic of the anchor facts written at turns 0..N — the growth
#: probes query this stable word from the very start of the session.
ANCHOR_TOPIC = "lighthouse"

#: Anchor facts at the head of the run — a SATURATING cluster for the
#: growth probes: with 12 anchor candidates the fixed top-10 recall is
#: full at the early probe already, so the factor measures composition
#: behaviour, not candidate-pool filling (4 anchors measured 2.56 —
#: budget filling, not purity).
_ANCHOR_COUNT = 12

#: Age-targeting tolerance for retention probes (bucket fuzz in turns).
_AGE_TOLERANCE = 3

#: Rewrite blocks must clear the default ``ccr.min_size_chars`` (500) with
#: margin — the run-time fixture guard asserts against this bound.
_REWRITE_MIN_CHARS = 600

#: Unique (adjective, noun) pool size — 40x40.
_PAIR_POOL = len(_ADJ) * len(_NOUN)

#: Facts per topic block (block assignment clusters a topic's facts in
#: time so sufficiency@task steps find full clusters early in the run).
_TOPIC_BLOCK = 8


@dataclass(frozen=True)
class ScenarioConfig:
    """Tunable axes of the simulated session (defaults: nightly shape)."""

    turns: int = 240
    seed: int = 42
    k: int = 5
    ages: tuple[int, ...] = (10, 50, 100, 200)
    #: Weights of the free-turn fill (write / retention search / assemble /
    #: rewrite). Scheduled ops (anchors, checkpoints, tasks, drift, growth)
    #: sit outside the weights.
    write_weight: float = 0.45
    search_weight: float = 0.30
    assemble_weight: float = 0.10
    rewrite_weight: float = 0.05
    #: Share of retention probes issued as paraphrase (rest exact).
    paraphrase_share: float = 0.35
    tasks: int = 5
    task_facts: int = 3
    checkpoints: int = 3
    drift_sample: int = 12
    checkpoint_audit_cap: int = 40
    assemble_budget: int = 2048
    growth_budget: int = 1024


@dataclass(frozen=True)
class FactSpec:
    """One recorded fact — the unit every F5 metric probes."""

    marker: str
    adjective: str
    noun: str
    topic: str
    turn: int
    content: str
    exact_query: str
    paraphrase_query: str
    is_anchor: bool = False


@dataclass(frozen=True)
class TurnOp:
    """Base — one logical turn."""


@dataclass(frozen=True)
class WriteFact(TurnOp):
    fact: FactSpec


@dataclass(frozen=True)
class SearchProbe(TurnOp):
    fact_marker: str
    mode: str  # "exact" | "paraphrase"
    purpose: str  # "retention" | "drift_early" | "drift_late" | "checkpoint_audit"


@dataclass(frozen=True)
class AssembleTurn(TurnOp):
    query: str
    budget: int
    purpose: str  # "traffic" | "growth_early" | "growth_late" | "task"
    required: tuple[str, ...] = ()


@dataclass(frozen=True)
class RewriteTurn(TurnOp):
    marker: str
    content: str


@dataclass(frozen=True)
class CheckpointTurn(TurnOp):
    checkpoint_id: str
    content: str
    audit_markers: tuple[str, ...]


@dataclass(frozen=True)
class Scenario:
    """The whole session as data — executable by ``run.py`` in one pass."""

    config: ScenarioConfig
    ops: tuple[TurnOp, ...]
    facts: tuple[FactSpec, ...]
    op_counts: dict[str, int] = field(default_factory=dict)


# ── Content builders (pure functions of their inputs) ────────────────────────


def _fact_content(marker: str, adjective: str, noun: str, topic: str) -> str:
    return (
        f"Session fact {marker}: during the {topic} workstream the {adjective} {noun} "
        f"agreement was recorded as binding for every later review. The {topic} "
        f"owner reaffirmed the {adjective} {noun} note after the follow-up audit, "
        f"and the crew indexed it for future sessions."
    )


def _anchor_content(marker: str, adjective: str, noun: str) -> str:
    return (
        f"Session anchor {marker}: the {adjective} {noun} lighthouse baseline "
        f"anchors the growth probe for this run. The lighthouse baseline text "
        f"stays stable while the session grows, so the assembled size must not "
        f"creep upward as entries accumulate."
    )


def _rewrite_content(marker: str, index: int) -> str:
    return (
        f"Rewrite block {marker} (session block #{index}): the working context "
        f"was compressed at this turn and the original block moves into memory "
        f"through the rewrite event. The block carries the reasoning trail of "
        f"the recent turns: what was decided, which facts were consulted, and "
        f"why the current shape of the plan won over the alternatives that "
        f"were tabled during the discussion. Later turns may need this "
        f"reasoning back when the plan is revisited; the marker left in the "
        f"compressed context redeems the original on demand through the "
        f"documented retrieval channel. This closing sentence exists to keep "
        f"the block above the CCR minimum size so a marker is always minted."
    )


def _checkpoint_content(checkpoint_id: str, index: int, total: int, facts_so_far: int) -> str:
    return (
        f"# Session checkpoint — {checkpoint_id}\n\n"
        f"## Goals\nContinue the long-lived session benchmark run.\n\n"
        f"## Completed\n{facts_so_far} facts recorded so far; retention probes active.\n\n"
        f"## In Progress\nSession epoch {index} of {total}; rewrite events ongoing.\n\n"
        f"## Context\nCheckpoint {index} of {total} for this seeded session.\n"
    )


def _assert_rewrite_fixture(block: str) -> None:
    """Fixture guard (fail loud, not a soft miss at run time)."""
    if len(block) < _REWRITE_MIN_CHARS:
        raise AssertionError(
            f"rewrite fixture block too short ({len(block)} chars) — a block below "
            "ccr.min_size_chars mints no marker and every later redemption would "
            "silently count as a mechanism miss; lengthen the block, never lower "
            "the threshold"
        )


# ── Scenario assembly ─────────────────────────────────────────────────────────


def build_scenario(config: ScenarioConfig) -> Scenario:
    """Generate the fixed turn list — pure data, no manager, no clock."""
    # The anchor head scales down for short runs (the suite smoke runs
    # 20 turns): saturation of the growth probe is a nightly-shape
    # concern, never a reason to reject a valid short session.
    anchor_count = min(_ANCHOR_COUNT, max(4, config.turns // 10))
    if config.turns < anchor_count + 4:
        raise ValueError(f"turns={config.turns} too small for an anchor head of {anchor_count}")
    if config.checkpoints < 1:
        raise ValueError("checkpoint-return-integrity needs at least one checkpoint")
    rng = random.Random(config.seed)
    turns = config.turns

    # Deterministic unique (adjective, noun) pairs — one per fact, ever.
    pool = [(a, n) for a in _ADJ for n in _NOUN]
    shuffled = rng.sample(pool, k=min(turns, _PAIR_POOL))
    pairs = [shuffled[i % len(shuffled)] for i in range(turns)]

    # ── 1. slot map: dict placeholders are (re)claimable decisions ──────
    ops: list[TurnOp | dict[str, Any]] = [{} for _ in range(turns)]

    taken: set[int] = set()
    ckpt_positions = _spread(
        turns, config.checkpoints, lo=0.30, hi=0.90, taken=taken, anchor_floor=anchor_count
    )
    task_positions = _spread(
        turns, config.tasks, lo=0.22, hi=0.86, taken=taken, anchor_floor=anchor_count
    )
    growth_early = max(anchor_count + 2, round(turns * 0.10))
    growth_late = turns - 1
    taken |= {growth_early, growth_late}
    for t in taken:
        ops[t] = {"kind": "scheduled"}

    # ── 2. free-slot fill by seeded weights (scheduled turns stay out) ──
    kinds = ("write", "search", "assemble", "rewrite")
    weights = (
        config.write_weight,
        config.search_weight,
        config.assemble_weight,
        config.rewrite_weight,
    )
    for t in range(turns):
        if t < anchor_count:
            ops[t] = {"kind": "write", "anchor": True}  # forced anchor head
        elif isinstance(ops[t], dict) and not ops[t]:
            ops[t] = {"kind": rng.choices(kinds, weights=weights, k=1)[0]}

    # ── 3. drift windows claim turns BEFORE facts materialize ───────────
    # (claiming after would fight already-placed WriteFact ops and the
    # windows would fragment — measured: 12 planned → 6 placed. The claim
    # RETIRES the slot's kind immediately so no fact is ever minted for a
    # turn the window later overwrites — a fact without a write op would
    # silently pollute every downstream metric pool.)
    early_cut = max(anchor_count, round(turns * 0.15))
    early_start = round(turns * 0.34)
    late_end = turns - 2  # the final turn is growth_late
    drift_want = config.drift_sample
    early_slots = _claim_kind_slots(ops, range(early_start, early_start + drift_want))
    late_slots = _claim_kind_slots(ops, range(late_end - drift_want + 1, late_end + 1))
    for t in (*early_slots, *late_slots):
        ops[t] = {"kind": "drift"}  # retired from every other fill path

    # ── 4. facts: turns come from the write slots that survived ────────
    write_turns = [
        t for t in range(turns) if isinstance(ops[t], dict) and ops[t].get("kind") == "write"
    ]
    anchors: list[FactSpec] = []
    regulars: list[FactSpec] = []

    def _make_fact(i: int, topic: str, *, anchor: bool, turn: int) -> FactSpec:
        adjective, noun = pairs[i]
        marker = f"{adjective}-{noun}-{i + 1}"
        if anchor:
            content = _anchor_content(marker, adjective, noun)
            paraphrase = f"{noun} {adjective} {ANCHOR_TOPIC}"
        else:
            content = _fact_content(marker, adjective, noun, topic)
            paraphrase = f"{noun} {adjective} {topic}"
        return FactSpec(
            marker=marker,
            adjective=adjective,
            noun=noun,
            topic=topic,
            turn=turn,
            content=content,
            exact_query=f"{adjective} {noun}",
            paraphrase_query=paraphrase,
            is_anchor=anchor,
        )

    for i, t in enumerate(write_turns):
        anchor = t < anchor_count
        # BLOCK topic assignment (not round-robin): each topic's facts
        # cluster in time, so an early sufficiency@task step finds a full
        # cluster (round-robin starved early tasks — measured: first task
        # at 22% of the run had no 3-fact topic).
        topic = ANCHOR_TOPIC if anchor else _TOPICS[(i // _TOPIC_BLOCK) % len(_TOPICS)]
        spec = _make_fact(i, topic, anchor=anchor, turn=t)
        (anchors if anchor else regulars).append(spec)
        ops[t] = WriteFact(spec)

    facts = sorted((*anchors, *regulars), key=lambda f: f.turn)

    # ── 5. drift probes over the claimed windows (same sample both ends) ─
    drift_pool = [f for f in facts if f.turn < early_cut]
    drift_sample = drift_pool[: min(len(early_slots), len(late_slots), drift_want)]
    for i, fact in enumerate(drift_sample):
        ops[early_slots[i]] = SearchProbe(fact.marker, "exact", "drift_early")
        ops[late_slots[i]] = SearchProbe(fact.marker, "exact", "drift_late")

    # ── 6. retention probes over the free search slots ──────────────────
    # Greedy age balancing: each probe targets the FEWEST-FILLED age bin
    # that has a fact within tolerance at this turn (ties → the larger
    # age: far bins are structurally scarcer — a plain forward cycle
    # measured 33/11/9/2 across the four bins).
    age_placed = {age: 0 for age in config.ages}
    for t in range(turns):
        slot = ops[t]
        if not isinstance(slot, dict) or slot.get("kind") != "search":
            continue
        chosen = _choose_probe_fact(facts, t, config.ages, age_placed)
        if chosen is None:
            ops[t] = {"kind": "assemble"}  # no fact near any target age yet
            continue
        best, target_age = chosen
        age_placed[target_age] += 1
        mode = "paraphrase" if rng.random() < config.paraphrase_share else "exact"
        ops[t] = SearchProbe(best.marker, mode, "retention")

    # ── 7. scheduled ops: checkpoints, tasks, growth probes ─────────────
    for i, t in enumerate(ckpt_positions):
        facts_before = [f for f in facts if f.turn < t]
        audit = _cap_sample(facts_before, config.checkpoint_audit_cap)
        cid = f"s3-ckpt-{i + 1}"
        ops[t] = CheckpointTurn(
            checkpoint_id=cid,
            content=_checkpoint_content(cid, i + 1, len(ckpt_positions), len(facts_before)),
            audit_markers=tuple(f.marker for f in audit),
        )

    tasks_placed = 0
    used_topics: set[str] = set()
    for t in task_positions:
        if tasks_placed >= config.tasks:
            break
        tasks_placed += _place_task(ops, t, facts, config, rng, used_topics)

    ops[growth_early] = AssembleTurn(ANCHOR_TOPIC, config.growth_budget, "growth_early")
    ops[growth_late] = AssembleTurn(ANCHOR_TOPIC, config.growth_budget, "growth_late")

    # ── 8. rewrite blocks + traffic assembles (materialize leftovers) ───
    rewrite_index = 0
    for t in range(turns):
        slot = ops[t]
        if not isinstance(slot, dict):
            continue
        if slot.get("kind") == "rewrite":
            rewrite_index += 1
            marker = f"rwb-{rewrite_index:03d}"
            block = _rewrite_content(marker, rewrite_index)
            _assert_rewrite_fixture(block)
            ops[t] = RewriteTurn(marker, block)
        else:  # "assemble", "scheduled" leftovers, de-targeted "search"
            ops[t] = AssembleTurn(_traffic_query(rng), config.assemble_budget, "traffic")

    final_ops = tuple(op for op in ops if not isinstance(op, dict))
    counts: dict[str, int] = {}
    for op in final_ops:
        name = type(op).__name__
        counts[name] = counts.get(name, 0) + 1
    assert len(final_ops) == turns, "every turn must carry exactly one op"
    return Scenario(config=config, ops=final_ops, facts=tuple(facts), op_counts=counts)


def _choose_probe_fact(
    facts: list[FactSpec], turn: int, ages: tuple[int, ...], age_placed: dict[int, int]
) -> tuple[FactSpec, int] | None:
    """Pick ``(fact, target_age)``: the fewest-filled feasible age bin.

    Strict feasibility keeps histogram buckets honest: an age-N probe is
    only issued when an ~N-turn-old fact exists (a fallback-to-nearest
    pollutes the far bins — measured mean age 186 in the 200 bin).
    """
    feasible: list[tuple[int, FactSpec]] = []
    for age in ages:
        within = [
            f for f in facts if f.turn < turn and abs((turn - f.turn) - age) <= _AGE_TOLERANCE
        ]
        if within:
            best = min(within, key=lambda f: (abs((turn - f.turn) - age), f.turn))
            feasible.append((age, best))
    if not feasible:
        return None
    age, best = min(feasible, key=lambda ab: (age_placed[ab[0]], -ab[0]))
    return best, age


def _claim_kind_slots(ops: list[TurnOp | dict[str, Any]], span: range) -> list[int]:
    """Turns in ``span`` still holding a kind decision (reclaimable)."""
    return [
        t
        for t in span
        if 0 <= t < len(ops) and isinstance(ops[t], dict) and ops[t].get("kind") != "scheduled"
    ]


def _spread(
    turns: int,
    n: int,
    *,
    lo: float,
    hi: float,
    taken: set[int],
    anchor_floor: int,
) -> list[int]:
    """``n`` positions evenly spaced in [lo, hi] fractions of the run.

    ``taken`` accumulates the claimed turns so scheduled families never
    collide on one turn (a collision would silently drop an op).
    """
    if n <= 0:
        return []
    span = hi - lo
    step = span / n if n > 1 else 0.0
    out: list[int] = []
    for i in range(n):
        frac = lo + (step * i if n > 1 else span / 2)
        pos = round(turns * frac)
        pos = max(anchor_floor + 1, min(turns - 2, pos))
        while pos in out or pos in taken:
            pos += 1
        out.append(pos)
        taken.add(pos)
    return sorted(out)


def _cap_sample(facts: list[FactSpec], cap: int) -> list[FactSpec]:
    """Evenly spaced deterministic subsample when the pool exceeds the cap."""
    if len(facts) <= cap:
        return list(facts)
    step = len(facts) / cap
    return [facts[min(len(facts) - 1, int(i * step))] for i in range(cap)]


def _place_task(
    ops: list[TurnOp | dict[str, Any]],
    turn: int,
    facts: list[FactSpec],
    config: ScenarioConfig,
    rng: random.Random,
    used_topics: set[str],
) -> int:
    """Place one sufficiency@task step at ``turn``; 1 if placed, 0 if not.

    Topic variety: prefer the fullest UNUSED topic so successive tasks
    measure different clusters (all-five-on-one-topic was the measured
    default of a plain argmax); reuse is allowed only when every
    eligible topic is already used.
    """
    by_topic: dict[str, list[FactSpec]] = {}
    for f in facts:
        if f.turn < turn and not f.is_anchor:
            by_topic.setdefault(f.topic, []).append(f)
    eligible = {tp: fs for tp, fs in by_topic.items() if len(fs) >= config.task_facts}
    if not eligible:
        ops[turn] = AssembleTurn(_traffic_query(rng), config.assemble_budget, "traffic")
        return 0
    fresh = {tp: fs for tp, fs in eligible.items() if tp not in used_topics} or eligible
    topic = max(fresh, key=lambda tp: (len(fresh[tp]), -_TOPICS.index(tp)))
    used_topics.add(topic)
    required = sorted(eligible[topic], key=lambda f: f.turn)[: config.task_facts]
    ops[turn] = AssembleTurn(
        query=topic,
        budget=config.assemble_budget,
        purpose="task",
        required=tuple(f.marker for f in required),
    )
    return 1


def _traffic_query(rng: random.Random) -> str:
    """A plausible pre-LLM-call hint — a topic token from the world."""
    return rng.choice(_TOPICS)


__all__ = [
    "ANCHOR_TOPIC",
    "AssembleTurn",
    "CheckpointTurn",
    "FactSpec",
    "RewriteTurn",
    "Scenario",
    "ScenarioConfig",
    "SearchProbe",
    "TurnOp",
    "WriteFact",
    "build_scenario",
]
