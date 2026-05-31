"""Tests for M5: Policy Engine, DLQ, triggers, scheduler.

Covers:
  - DLQ: add, list, retry, discard, count, ready-only filter
  - Policy engine: rule evaluation, condition matching, YAML loading
  - Triggers: on_memory_saved, on_status_changed
  - Scheduler: auto_cluster, auto_synthesize, auto_publish, dlq_retry
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mnemos.config import Settings
from mnemos.manager import MemoryManager
from mnemos.models import Memory, MemoryStatus
from mnemos.policy.dlq import dlq_add, dlq_discard, dlq_list, dlq_retry
from mnemos.policy.engine import (
    PolicyAction,
    PolicyCondition,
    PolicyRule,
    evaluate_rules,
    load_rules_from_dict,
)
from mnemos.policy.scheduler import (
    auto_cluster,
    auto_publish,
    auto_synthesize,
    dlq_retry_scheduler,
)
from mnemos.policy.triggers import on_memory_saved, on_status_changed

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_settings():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        settings = Settings(
            mnemos={
                "vault_path": str(tmp / "vault"),
                "data_dir": str(tmp / "data"),
                "db_name": "test.db",
            },
            embedding={"provider": "onnx"},
            automation={
                "enabled": True,
                "min_raw_to_trigger": 1,
            },
        )
        settings.resolve_paths()
        yield settings


@pytest.fixture
def tmp_manager(tmp_settings):
    mgr = MemoryManager(tmp_settings)
    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [0.1] * 384
    mgr._embedder = mock_embedder
    yield mgr
    mgr.close()


# ---------------------------------------------------------------------------
# DLQ
# ---------------------------------------------------------------------------


class TestDLQ:
    def test_add_and_list(self, tmp_manager):
        """DLQ entries are persisted and retrievable."""
        mgr = tmp_manager
        dlq_add(mgr, "mem-123", task_label="synthesize", error_message="LLM timeout")
        entries = dlq_list(mgr)
        assert len(entries) == 1
        assert entries[0]["memory_id"] == "mem-123"
        assert entries[0]["task_label"] == "synthesize"

    def test_list_filters_by_task_label(self, tmp_manager):
        """task_label filter narrows DLQ list."""
        mgr = tmp_manager
        dlq_add(mgr, "m1", task_label="synthesize")
        dlq_add(mgr, "m2", task_label="publish")

        syn = dlq_list(mgr, task_label="synthesize")
        pub = dlq_list(mgr, task_label="publish")
        assert len(syn) == 1
        assert len(pub) == 1
        assert syn[0]["memory_id"] == "m1"

    def test_retry_increments_attempt(self, tmp_manager):
        """dlq_retry bumps attempt_count and sets next_retry_at."""
        mgr = tmp_manager
        dlq_add(mgr, "m1", max_attempts=3)
        entry = dlq_list(mgr)[0]
        assert entry["attempt_count"] == 1

        dlq_retry(mgr, entry["id"], backoff_sec=10)
        updated = dlq_list(mgr)[0]
        assert updated["attempt_count"] == 2
        assert updated["next_retry_at"] is not None

    def test_discard_removes_entry(self, tmp_manager):
        """dlq_discard deletes the entry."""
        mgr = tmp_manager
        dlq_add(mgr, "m1")
        entry = dlq_list(mgr)[0]

        ok = dlq_discard(mgr, entry["id"])
        assert ok is True
        assert dlq_list(mgr) == []

    def test_count(self, tmp_manager):
        """dlq_count reflects current entries."""
        mgr = tmp_manager
        assert mgr.sqlite.dlq_count() == 0
        dlq_add(mgr, "m1")
        assert mgr.sqlite.dlq_count() == 1
        dlq_add(mgr, "m2")
        assert mgr.sqlite.dlq_count() == 2

    def test_ready_only_filter(self, tmp_manager):
        """ready_only=True filters to entries with past next_retry_at."""
        mgr = tmp_manager
        dlq_add(mgr, "m1")
        # Default next_retry_at is now, so it should be ready
        ready = dlq_list(mgr, ready_only=True)
        assert len(ready) == 1

    def test_max_attempts_respected(self, tmp_manager):
        """Entries store max_attempts correctly."""
        mgr = tmp_manager
        dlq_add(mgr, "m1", max_attempts=5)
        entry = dlq_list(mgr)[0]
        assert entry["max_attempts"] == 5


# ---------------------------------------------------------------------------
# Policy engine
# ---------------------------------------------------------------------------


class TestPolicyEngine:
    def test_single_rule_matches(self):
        """A rule with matching conditions fires its actions."""
        mem = Memory(
            content="draft",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
            status=MemoryStatus.PROCESSED,
            quality_score=0.9,
            confidence=0.9,
            source_coverage=5,
        )
        rule = PolicyRule(
            name="auto-publish-high-quality",
            conditions=[
                PolicyCondition(status=MemoryStatus.PROCESSED, min_quality=0.8),
            ],
            actions=[PolicyAction(action="publish")],
        )
        actions = evaluate_rules(mem, [rule])
        assert len(actions) == 1
        assert actions[0].action == "publish"

    def test_rule_fails_on_mismatch(self):
        """A rule with non-matching conditions does NOT fire."""
        mem = Memory(
            content="draft",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
            status=MemoryStatus.RAW,
            quality_score=0.9,
        )
        rule = PolicyRule(
            name="only-processed",
            conditions=[PolicyCondition(status=MemoryStatus.PROCESSED)],
            actions=[PolicyAction(action="publish")],
        )
        actions = evaluate_rules(mem, [rule])
        assert actions == []

    def test_multiple_conditions_all_must_match(self):
        """ALL conditions in a rule must match for it to fire."""
        mem = Memory(
            content="draft",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
            status=MemoryStatus.PROCESSED,
            quality_score=0.5,
            confidence=0.9,
        )
        rule = PolicyRule(
            name="multi-condition",
            conditions=[
                PolicyCondition(status=MemoryStatus.PROCESSED, min_quality=0.8),
                PolicyCondition(min_confidence=0.8),
            ],
            actions=[PolicyAction(action="publish")],
        )
        actions = evaluate_rules(mem, [rule])
        assert actions == []

    def test_disabled_rule_ignored(self):
        """enabled=False prevents rule from firing."""
        mem = Memory(
            content="draft",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
            status=MemoryStatus.PROCESSED,
        )
        rule = PolicyRule(
            name="disabled",
            enabled=False,
            conditions=[PolicyCondition(status=MemoryStatus.PROCESSED)],
            actions=[PolicyAction(action="publish")],
        )
        actions = evaluate_rules(mem, [rule])
        assert actions == []

    def test_priority_sorting(self):
        """Higher-priority rules are evaluated first (affects logging, not logic)."""
        mem = Memory(
            content="draft",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
            status=MemoryStatus.PROCESSED,
        )
        low = PolicyRule(
            name="low",
            priority=1,
            conditions=[PolicyCondition(status=MemoryStatus.PROCESSED)],
            actions=[PolicyAction(action="archive")],
        )
        high = PolicyRule(
            name="high",
            priority=10,
            conditions=[PolicyCondition(status=MemoryStatus.PROCESSED)],
            actions=[PolicyAction(action="publish")],
        )
        actions = evaluate_rules(mem, [low, high])
        assert len(actions) == 2
        # high priority first
        assert actions[0].action == "publish"
        assert actions[1].action == "archive"

    def test_tags_include_filter(self):
        """tags_include requires all listed tags to be present."""
        mem = Memory(
            content="draft",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
            status=MemoryStatus.PROCESSED,
        )
        rule = PolicyRule(
            name="tag-filter",
            conditions=[PolicyCondition(tags_include=["gcw:learning"])],
            actions=[PolicyAction(action="publish")],
        )
        actions = evaluate_rules(mem, [rule])
        assert len(actions) == 1

    def test_tags_exclude_filter(self):
        """tags_exclude prevents firing if any listed tag is present."""
        mem = Memory(
            content="draft",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
            status=MemoryStatus.PROCESSED,
        )
        rule = PolicyRule(
            name="exclude-tag",
            conditions=[PolicyCondition(tags_exclude=["gcw:learning"])],
            actions=[PolicyAction(action="publish")],
        )
        actions = evaluate_rules(mem, [rule])
        assert actions == []

    def test_load_rules_from_dict(self):
        """YAML-like dict parses into PolicyRule list."""
        raw = {
            "rules": {
                "auto-publish": {
                    "enabled": True,
                    "priority": 5,
                    "conditions": [
                        {"status": "processed", "min_quality": 0.7},
                    ],
                    "actions": [{"action": "publish"}],
                },
            },
        }
        rules = load_rules_from_dict(raw)
        assert len(rules) == 1
        assert rules[0].name == "auto-publish"
        assert rules[0].actions[0].action == "publish"

    def test_empty_rules_list(self):
        """No rules → no actions."""
        mem = Memory(content="x", tags=["project:p", "agent:a", "gcw:learning"])
        actions = evaluate_rules(mem, [])
        assert actions == []


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


class TestTriggers:
    def test_on_memory_saved_no_rules(self, tmp_manager):
        """Trigger with no configured rules is a no-op."""
        mgr = tmp_manager
        mem = Memory(
            content="note",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
        )
        on_memory_saved(mgr, mem)  # should not raise

    def test_on_status_changed_processed(self, tmp_manager):
        """Transition to processed may auto-publish if policy matches."""
        mgr = tmp_manager
        # Seed a policy that auto-publishes processed memories
        mgr.settings.policies = {
            "rules": {
                "auto-pub": {
                    "enabled": True,
                    "conditions": [{"status": "processed"}],
                    "actions": [{"action": "publish"}],
                },
            },
        }
        mem = Memory(
            content="draft",
            tags=["project:gcw", "agent:reviewer", "gcw:learning"],
            project="gcw",
            agent="reviewer",
            status=MemoryStatus.PROCESSED,
        )
        mgr.sqlite.save(mem)
        mgr._embedder = MagicMock()
        mgr._embedder.embed.return_value = [0.2] * 384

        on_status_changed(mgr, mem, MemoryStatus.RAW, MemoryStatus.PROCESSED)
        reloaded = mgr.sqlite.get(mem.id)
        assert reloaded.status == MemoryStatus.PUBLISHED


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class TestScheduler:
    def test_auto_cluster_skips_when_disabled(self, tmp_manager):
        """auto_cluster is a no-op when automation disabled."""
        mgr = tmp_manager
        mgr.settings.automation.enabled = False
        auto_cluster(mgr)  # should not raise

    def test_auto_cluster_skips_below_min_raw(self, tmp_manager):
        """auto_cluster skips if raw count < min_raw_to_trigger."""
        mgr = tmp_manager
        mgr.settings.automation.min_raw_to_trigger = 10
        auto_cluster(mgr)
        # No crash, no clusters created
        assert mgr.sqlite.count_by_status().get("raw", 0) < 10

    def test_auto_synthesize_no_processing(self, tmp_manager):
        """auto_synthesize with no processing memories is a no-op."""
        mgr = tmp_manager
        auto_synthesize(mgr)  # should not raise

    def test_auto_publish_no_processed(self, tmp_manager):
        """auto_publish with no processed memories is a no-op."""
        mgr = tmp_manager
        auto_publish(mgr)  # should not raise

    def test_dlq_retry_scheduler_empty(self, tmp_manager):
        """dlq_retry_scheduler with empty DLQ is a no-op."""
        mgr = tmp_manager
        dlq_retry_scheduler(mgr)  # should not raise
        assert mgr.sqlite.dlq_count() == 0

    def test_dlq_retry_scheduler_max_retries(self, tmp_manager):
        """Entries at max attempts are discarded by scheduler."""
        mgr = tmp_manager
        dlq_add(mgr, "m1", max_attempts=1)
        entry = dlq_list(mgr)[0]
        # Simulate one retry already done
        mgr.sqlite.dlq_increment_attempt(entry["id"], backoff_sec=1)

        dlq_retry_scheduler(mgr)
        assert mgr.sqlite.dlq_count() == 0
