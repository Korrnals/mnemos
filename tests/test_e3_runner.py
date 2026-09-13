"""E3 lanes runner — smoke tests over the REAL strata (issue #277).

These are RUNNER-MACHINERY gates, not an experiment run: collect-only
executes the legs, validates the artifacts and persists NOTHING. The
first recorded E3 run stays a deliberate human decision after this
infrastructure merges (E0 anti-HARKing window).

Pins:

* dry-run over the real G-gov/G-neg strata produces a WELL-FORMED
  manifest + paired outcomes without persisting a run;
* the default invocation REFUSES to record (message + zero writes);
  ``--record`` writes write-once artifacts; a re-record fails loud;
* ledger-hash coupling — a mutated ledger changes the manifest's ledger
  hash and run id (replacements change the analyzed qid set);
* leg configuration — A flag-off, B0 type-boost, B lanes-on, equal
  budget everywhere (E0 §1.1, §4.1).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from benchmarks.experiments.e3_lanes import runner
from benchmarks.strata.e2_gov import ground_truth as gt

from mnemos.lanes import B0_TYPE_BOOST_FACTOR

#: The S1 corpus fingerprint pinned upstream (test_strata_e2_gov) — the
#: golden 81 inside the combined build must stay byte-identical.
_S1_PINNED_FINGERPRINT = "c2ce056d57d91143f7a1959442ef2b37891464d4cd5f218f5eabbc785c8e72f1"

_QID_RE = re.compile(r"^gg-\d{3}-(ph|pr)$")


@pytest.fixture(scope="module")
def collected() -> Iterator[tuple[dict, dict]]:
    """One full collect over the real strata (all three legs)."""
    manifest, outcomes = runner.collect_run(gt.initial_ledger())
    yield manifest, outcomes


# ── dry-run over the real strata ─────────────────────────────────────────────


def test_dry_run_manifest_well_formed(collected: tuple[dict, dict]) -> None:
    manifest, _ = collected
    runner.verify_manifest(manifest)  # raises on any drift
    assert manifest["experiment"] == "e3-lanes"
    assert manifest["denominator"] == 96
    assert manifest["equal_budget"] == runner.E3_TOKEN_BUDGET
    assert manifest["top_k"] == 5
    legs = manifest["legs"]
    assert legs["A"] == {"lanes_enabled": False, "type_boost": False}
    assert legs["B0"] == {
        "lanes_enabled": False,
        "type_boost": True,
        "b0_type_boost_factor": B0_TYPE_BOOST_FACTOR,
    }
    assert legs["B"] == {"lanes_enabled": True, "type_boost": False}
    # ledger coupling: the manifest hash covers the committed state
    assert manifest["ledger"]["state_sha256"] == runner.ledger_state_hash(gt.load_ledger())
    assert manifest["ledger"]["analyzed_records"] == 48
    # corpus fingerprints: e2-gov live + the pinned S1 corpus (golden 81)
    assert manifest["corpus"]["combined_total"] == 421
    assert len(manifest["corpus"]["e2_gov_corpus_fingerprint"]) == 64
    assert manifest["corpus"]["s1_corpus_fingerprint"] == _S1_PINNED_FINGERPRINT
    # code version captured
    code = manifest["code"]
    assert code["mnemos_version"] and code["version_file"]
    assert code["git_commit"] not in ("", None)


def test_dry_run_outcomes_paired_and_complete(collected: tuple[dict, dict]) -> None:
    _, outcomes = collected
    runner.verify_outcomes(outcomes)
    assert outcomes["legs"] == ["A", "B0", "B"]
    pairs = outcomes["pairs"]
    assert len(pairs) == 96
    qids = [p["qid"] for p in pairs]
    assert len(set(qids)) == 96
    assert all(_QID_RE.match(q) for q in qids)  # pairing keys gg-NNN-ph/pr
    families: dict[str, set[str]] = {}
    for p in pairs:
        families.setdefault(p["record_slug"], set()).add(p["family"])
    assert all(f == {"ph", "pr"} for f in families.values())  # two phrasings per record
    for p in pairs:
        for leg in ("A", "B0", "B"):
            assert isinstance(p[leg], bool)
    for leg in outcomes["legs"]:
        assert len(outcomes["g_gov"][leg]) == 96
        assert len(outcomes["g_neg"][leg]) == 24
        for row in outcomes["g_neg"][leg]:
            assert isinstance(row["false_insertion"], bool)
            assert isinstance(row["inserted_governance_slugs"], list)


def test_discordance_tallies_consistent_with_pairs(collected: tuple[dict, dict]) -> None:
    """The persisted McNemar inputs are counts over the raw pairs — no
    statistics beyond tallies at run time (E0 §6.1/§6.6)."""
    _, outcomes = collected
    pairs = outcomes["pairs"]
    assert outcomes["discordance"]["B_vs_A"] == {
        "A_only": sum(1 for p in pairs if p["A"] and not p["B"]),
        "B_only": sum(1 for p in pairs if p["B"] and not p["A"]),
    }
    assert outcomes["discordance"]["B_vs_B0"] == {
        "B0_only": sum(1 for p in pairs if p["B0"] and not p["B"]),
        "B_only": sum(1 for p in pairs if p["B"] and not p["B0"]),
    }
    # rates are plain proportions of the raw rows
    for leg in outcomes["legs"]:
        hits = sum(1 for p in pairs if p[leg])
        assert outcomes["leg_rates"][leg]["governance_recall_at_5"] == hits / 96
    assert not any(key in json.dumps(outcomes) for key in ('"p_value"', '"ci95"', '"verdict"'))


def test_collect_is_deterministic(collected: tuple[dict, dict]) -> None:
    """Same ledger + corpus + code → same run id and identical outcomes
    (the deterministic embedder + fixed corpus order make the legs
    reproducible; the manifest core is content-addressed)."""
    first_manifest, first_outcomes = collected
    second_manifest, second_outcomes = runner.collect_run(gt.initial_ledger())
    assert second_manifest["run_id"] == first_manifest["run_id"]
    assert second_outcomes["pairs"] == first_outcomes["pairs"]
    assert second_outcomes["g_neg"] == first_outcomes["g_neg"]


# ── refusal and write-once recording ─────────────────────────────────────────


def test_default_invocation_refuses_to_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No --record → the run executes, the refusal prints, NOTHING is
    written under the runs directory."""
    rc = runner.main(["--runs-dir", str(tmp_path), "--quiet"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "REFUSING to record" in err
    assert not list(tmp_path.iterdir()), "collect-only must not persist anything"


def test_record_writes_write_once_artifacts(tmp_path: Path, collected: tuple[dict, dict]) -> None:
    manifest, outcomes = collected
    run_dir = runner.record_run(manifest, outcomes, tmp_path)
    assert run_dir == tmp_path / manifest["run_id"]
    on_disk = json.loads((run_dir / "manifest.json").read_text())
    runner.verify_manifest(on_disk)
    assert on_disk["manifest_sha256"] == manifest["manifest_sha256"]
    outcomes_disk = json.loads((run_dir / "outcomes.json").read_text())
    assert outcomes_disk["run_id"] == manifest["run_id"]
    assert outcomes_disk["pairs"] == outcomes["pairs"]
    # write-once: the same run id can never be overwritten
    with pytest.raises(FileExistsError, match="write-once"):
        runner.record_run(manifest, outcomes, tmp_path)
    # and the CLI surfaces the refusal as a clean non-zero exit
    rc = runner.main(["--record", "--runs-dir", str(tmp_path), "--quiet"])
    assert rc == 1


def test_record_refuses_mismatched_artifacts(tmp_path: Path) -> None:
    """A manifest whose integrity hash was tampered with never reaches
    the disk."""
    manifest = runner.finalize_manifest(runner.build_manifest(gt.initial_ledger()))
    tampered = {**manifest, "equal_budget": 999999}
    with pytest.raises(AssertionError):
        runner.record_run(tampered, {"pairs": []}, tmp_path)  # type: ignore[arg-type]
    assert not list(tmp_path.iterdir())


# ── ledger-hash coupling (issue #277, the run-manifest obligation) ───────────


def test_ledger_mutation_changes_manifest_hash() -> None:
    """A replacement changes the analyzed qid set — the manifest's
    ledger hash, qid-set hash and run id must all move with it."""
    ledger = gt.initial_ledger()
    amended, _replacement = gt.record_rejection(
        ledger, ledger["analyzed_records"][7], "self-sufficiency fail", "2026-09-13"
    )
    base = runner.finalize_manifest(runner.build_manifest(ledger))
    moved = runner.finalize_manifest(runner.build_manifest(amended))
    assert base["ledger"]["state_sha256"] != moved["ledger"]["state_sha256"]
    assert base["run_id"] != moved["run_id"]
    assert base["queries"]["active_qid_set_sha256"] != moved["queries"]["active_qid_set_sha256"]
    assert moved["ledger"]["replacements"] == 1
    assert moved["queries"]["analyzed"] == 96  # denominator held (E0 §3.1)
