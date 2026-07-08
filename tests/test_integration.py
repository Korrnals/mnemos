"""Tests for the Mnemos integration layer (``mnemos integration *`` commands).

Covers:
* ``targets.yaml`` parsing and detection logic
* Version stamping (inject, replace, read)
* Deploy / verify / update / uninstall lifecycle
* Idempotency (re-deploy doesn't duplicate)
* Stale file detection
* User-file safety (uninstall never deletes unstamped files)
* CLI smoke tests via Typer's CliRunner
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from mnemos.cli.integration import (
    DeployStatus,
    IntegrationManager,
    Target,
    TargetsConfig,
    load_targets,
    make_stamp,
    read_stamp,
    stamp_content,
)
from mnemos.cli.main import app

runner = CliRunner()


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_pack(tmp_path: Path) -> Path:
    """Build a minimal integrations/ pack in tmp_path."""
    pack = tmp_path / "integrations"
    (pack / "instructions").mkdir(parents=True)
    (pack / "skills").mkdir(parents=True)
    (pack / "prompts").mkdir(parents=True)

    (pack / "instructions" / "mnemos-memory.instructions.md").write_text(
        "---\napplyTo: '**'\n---\n# Mnemos memory trigger\nUse mnemos tools.\n",
        encoding="utf-8",
    )
    skill_dir = pack / "skills" / "mnemos-recall"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "# Mnemos recall skill\n\nRecall context from memory.\n", encoding="utf-8"
    )
    (pack / "prompts" / "mnemos-session.prompt.md").write_text(
        "# Mnemos session prompt\n\nStart a memory-aware session.\n", encoding="utf-8"
    )
    (pack / "targets.yaml").write_text(
        yaml.dump(
            {
                "targets": {
                    "test-harness": {
                        "detect": [{"path": str(tmp_path / "harness-marker")}],
                        "deploy": {
                            "instructions": str(tmp_path / "deploy" / "instructions") + "/",
                            "skills": str(tmp_path / "deploy" / "skills") + "/",
                            "prompts": str(tmp_path / "deploy" / "prompts") + "/",
                        },
                        "format": "copy",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    # Create the detect marker so the target is "detected".
    (tmp_path / "harness-marker").mkdir(parents=True, exist_ok=True)
    return pack


@pytest.fixture
def manager(fake_pack: Path) -> IntegrationManager:
    """Build a manager pointed at the fake pack, version 1.2.0."""
    cfg = load_targets(fake_pack / "targets.yaml")
    return IntegrationManager(version="1.2.0", pack_root=fake_pack, targets_config=cfg)


@pytest.fixture
def detected_target(manager: IntegrationManager) -> str:
    """Return the name of the (single) detected target."""
    detected = manager.targets.detected()
    assert len(detected) == 1
    return detected[0].name


# ── targets.yaml parsing ──────────────────────────────────────────────────────


class TestTargetsConfig:
    def test_load_targets_parses_all_fields(self, fake_pack: Path) -> None:
        cfg = load_targets(fake_pack / "targets.yaml")
        assert len(cfg.targets) == 1
        t = cfg.targets[0]
        assert t.name == "test-harness"
        assert len(t.detect_paths) == 1
        assert t.deploy_map["instructions"].is_absolute()
        assert t.format == "copy"

    def test_load_targets_default_path(self) -> None:
        """load_targets() with no arg resolves the shipped targets.yaml."""
        cfg = load_targets()
        names = [t.name for t in cfg.targets]
        assert "copilot" in names
        assert "generic-copilot" in names
        assert "cursor" in names

    def test_is_detected_true_when_path_exists(self, fake_pack: Path) -> None:
        cfg = load_targets(fake_pack / "targets.yaml")
        assert cfg.targets[0].is_detected()

    def test_is_detected_false_when_path_missing(self, tmp_path: Path) -> None:
        t = Target(
            name="ghost",
            detect_paths=(tmp_path / "nonexistent",),
            deploy_map={},
        )
        assert not t.is_detected()

    def test_load_targets_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_targets(tmp_path / "nope.yaml")

    def test_load_targets_invalid_structure(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("just: a string", encoding="utf-8")
        with pytest.raises(ValueError):
            load_targets(bad)

    def test_tilde_expansion(self) -> None:
        """Detect paths with ~ are expanded to absolute."""
        cfg = load_targets()
        for t in cfg.targets:
            for p in t.detect_paths:
                assert "~" not in str(p), f"unexpanded tilde in {t.name}: {p}"


# ── Version stamping ───────────────────────────────────────────────────────────


class TestStamping:
    def test_make_stamp_format(self) -> None:
        assert make_stamp("1.2.0") == "<!-- mnemos-integration: v1.2.0 -->"

    def test_read_stamp_extracts_version(self) -> None:
        content = "<!-- mnemos-integration: v1.2.0 -->\n# Some file\n"
        assert read_stamp(content) == "1.2.0"

    def test_read_stamp_returns_none_when_absent(self) -> None:
        assert read_stamp("# just a file\n") is None

    def test_stamp_content_injects_stamp(self) -> None:
        content = "# Title\n\nbody\n"
        stamped = stamp_content(content, "1.2.0")
        assert read_stamp(stamped) == "1.2.0"

    def test_stamp_content_preserves_body(self) -> None:
        content = "# Title\n\nbody text\n"
        stamped = stamp_content(content, "1.2.0")
        assert "# Title" in stamped
        assert "body text" in stamped

    def test_stamp_content_after_frontmatter(self) -> None:
        content = "---\napplyTo: '**'\n---\n# Title\n"
        stamped = stamp_content(content, "1.2.0")
        lines = stamped.splitlines()
        # Stamp should come after the front-matter block.
        stamp_idx = next(i for i, line in enumerate(lines) if "mnemos-integration" in line)
        fm_end_idx = next(i for i, line in enumerate(lines) if line.strip() == "---" and i > 0)
        assert stamp_idx > fm_end_idx

    def test_stamp_content_replaces_existing_stamp(self) -> None:
        content = "<!-- mnemos-integration: v1.1.0 -->\n# Title\n"
        stamped = stamp_content(content, "1.2.0")
        assert read_stamp(stamped) == "1.2.0"
        assert "v1.1.0" not in stamped

    def test_stamp_content_idempotent(self) -> None:
        content = "# Title\nbody\n"
        once = stamp_content(content, "1.2.0")
        twice = stamp_content(once, "1.2.0")
        assert once == twice

    def test_stamp_after_shebang(self) -> None:
        content = "#!/bin/bash\necho hi\n"
        stamped = stamp_content(content, "1.2.0")
        lines = stamped.splitlines()
        assert lines[0] == "#!/bin/bash"
        assert "mnemos-integration" in lines[1]


# ── Deploy / verify / update / uninstall lifecycle ────────────────────────────


class TestDeploy:
    def test_deploy_creates_stamped_files(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        result = manager.deploy(detected_target)
        assert result.deployed_count == 3  # instructions + skills + prompts

        # Check files exist and are stamped.
        for f in result.files:
            if f.status == DeployStatus.DEPLOYED:
                assert f.destination.exists()
                content = f.destination.read_text(encoding="utf-8")
                assert read_stamp(content) == "1.2.0"

    def test_deploy_preserves_subdirectory_structure(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        manager.deploy(detected_target)
        # skills/mnemos-recall/SKILL.md should land in deploy/skills/mnemos-recall/SKILL.md
        cfg = load_targets(manager.pack_root / "targets.yaml")
        dest = cfg.targets[0].deploy_map["skills"] / "mnemos-recall" / "SKILL.md"
        assert dest.exists()

    def test_deploy_dry_run_does_not_write(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        result = manager.deploy(detected_target, dry_run=True)
        assert result.deployed_count == 3
        for f in result.files:
            if f.status == DeployStatus.DEPLOYED:
                assert not f.destination.exists()

    def test_deploy_idempotent_second_run_current(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        manager.deploy(detected_target)
        result = manager.deploy(detected_target)
        assert all(f.status == DeployStatus.CURRENT for f in result.files)
        assert result.deployed_count == 0

    def test_deploy_unknown_target_raises(self, manager: IntegrationManager) -> None:
        with pytest.raises(ValueError, match="Unknown target"):
            manager.deploy("nonexistent")


class TestVerify:
    def test_verify_reports_missing_before_deploy(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        result = manager.verify(detected_target)
        assert result.missing_count == 3
        assert not result.all_current

    def test_verify_all_current_after_deploy(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        manager.deploy(detected_target)
        result = manager.verify(detected_target)
        assert result.all_current
        assert result.stale_count == 0
        assert result.missing_count == 0

    def test_verify_detects_stale_files(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        # Deploy with old version.
        old_mgr = IntegrationManager(
            version="1.1.0",
            pack_root=manager.pack_root,
            targets_config=manager.targets,
        )
        old_mgr.deploy(detected_target)

        # Verify with current version.
        result = manager.verify(detected_target)
        assert result.stale_count == 3
        assert not result.all_current

    def test_verify_skips_user_files(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        cfg = load_targets(manager.pack_root / "targets.yaml")
        dest_dir = cfg.targets[0].deploy_map["instructions"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        user_file = dest_dir / "user-custom.md"
        user_file.write_text("# My custom instruction\n", encoding="utf-8")

        manager.deploy(detected_target)
        result = manager.verify(detected_target)

        # User file should be SKIPPED, not MISSING or STALE.
        user_result = next(f for f in result.files if f.destination == user_file)
        assert user_result.status == DeployStatus.SKIPPED


class TestUpdate:
    def test_update_brings_stale_to_current(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        # Deploy old version.
        old_mgr = IntegrationManager(
            version="1.1.0",
            pack_root=manager.pack_root,
            targets_config=manager.targets,
        )
        old_mgr.deploy(detected_target)

        # Update to current.
        result = manager.update(detected_target)
        assert all(f.status == DeployStatus.UPDATED for f in result.files)

        # Verify now current.
        verify = manager.verify(detected_target)
        assert verify.all_current

    def test_update_dry_run_does_not_write(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        old_mgr = IntegrationManager(
            version="1.1.0",
            pack_root=manager.pack_root,
            targets_config=manager.targets,
        )
        old_mgr.deploy(detected_target)

        manager.update(detected_target, dry_run=True)
        # Files should still be old version.
        result = manager.verify(detected_target)
        assert result.stale_count == 3


class TestUninstall:
    def test_uninstall_removes_only_stamped_files(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        manager.deploy(detected_target)

        # Add a user file alongside.
        cfg = load_targets(manager.pack_root / "targets.yaml")
        dest_dir = cfg.targets[0].deploy_map["instructions"]
        user_file = dest_dir / "user-custom.md"
        user_file.write_text("# My custom\n", encoding="utf-8")

        result = manager.uninstall(detected_target)
        # All 3 pack files are stamped and removed.
        assert len(result.removed) == 3
        # User file is preserved.
        assert user_file.exists()
        assert user_file in result.skipped_user_files

    def test_uninstall_dry_run_does_not_delete(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        manager.deploy(detected_target)
        result = manager.uninstall(detected_target, dry_run=True)
        assert len(result.removed) == 3
        # Files should still exist.
        for f in result.removed:
            assert f.exists()

    def test_uninstall_no_stamped_files(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        # Deploy user files only (no stamp).
        cfg = load_targets(manager.pack_root / "targets.yaml")
        for kind_dir in cfg.targets[0].deploy_map.values():
            kind_dir.mkdir(parents=True, exist_ok=True)
            (kind_dir / "user.md").write_text("# user\n", encoding="utf-8")

        result = manager.uninstall(detected_target)
        assert len(result.removed) == 0
        assert len(result.skipped_user_files) == 3

    def test_uninstall_cleans_empty_dirs(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        manager.deploy(detected_target)
        cfg = load_targets(manager.pack_root / "targets.yaml")
        skills_dir = cfg.targets[0].deploy_map["skills"] / "mnemos-recall"

        manager.uninstall(detected_target)
        # The nested skill directory should be cleaned up.
        assert not skills_dir.exists()


# ── CLI smoke tests ───────────────────────────────────────────────────────────


class TestCLI:
    def test_integration_help_exits_cleanly(self) -> None:
        result = runner.invoke(app, ["integration", "--help"])
        assert result.exit_code == 0
        assert "detect" in result.output
        assert "setup" in result.output
        assert "verify" in result.output
        assert "uninstall" in result.output

    def test_integration_detect_runs(self) -> None:
        result = runner.invoke(app, ["integration", "detect"])
        assert result.exit_code == 0

    def test_integration_setup_unknown_target(self) -> None:
        result = runner.invoke(app, ["integration", "setup", "--target", "nonexistent"])
        assert result.exit_code == 1
        assert "Unknown target" in result.output

    def test_integration_setup_dry_run_no_files(self, fake_pack: Path, tmp_path: Path) -> None:
        """Dry-run should not create any files."""
        cfg = load_targets(fake_pack / "targets.yaml")
        mgr = IntegrationManager(version="1.2.0", pack_root=fake_pack, targets_config=cfg)
        result = mgr.setup("test-harness", dry_run=True, register_mcp=False)
        assert result.deployed_count == 3
        # No files should exist on disk.
        for f in result.files:
            if f.status == DeployStatus.DEPLOYED:
                assert not f.destination.exists()

    def test_integration_verify_exits_nonzero_when_stale(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        # Deploy old version.
        old_mgr = IntegrationManager(
            version="1.1.0",
            pack_root=manager.pack_root,
            targets_config=manager.targets,
        )
        old_mgr.deploy(detected_target)

        # Verify via CLI — should exit 1.
        result = runner.invoke(app, ["integration", "verify", "--target", detected_target])
        assert result.exit_code == 1

    def test_integration_verify_exits_zero_when_current(
        self, manager: IntegrationManager, detected_target: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI verify exits 0 when all files are current.

        We monkeypatch the CLI's _manager() and load_targets to use our
        fake pack so the test doesn't depend on the real ~/.copilot/ layout.
        """
        manager.deploy(detected_target)

        import mnemos.cli.util as util_mod

        monkeypatch.setattr(util_mod, "_manager", lambda pack_root=None: manager)
        monkeypatch.setattr(util_mod, "load_targets", lambda config_path=None: manager.targets)

        result = runner.invoke(app, ["integration", "verify", "--target", detected_target])
        assert result.exit_code == 0

    def test_full_lifecycle_via_manager(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """End-to-end: deploy → verify → update → uninstall."""
        # Deploy
        deploy_result = manager.deploy(detected_target)
        assert deploy_result.deployed_count == 3

        # Verify current
        verify = manager.verify(detected_target)
        assert verify.all_current

        # Uninstall
        uninstall = manager.uninstall(detected_target)
        assert len(uninstall.removed) == 3

        # Verify missing after uninstall
        verify2 = manager.verify(detected_target)
        assert verify2.missing_count == 3


# ── QA: additional edge-case coverage ─────────────────────────────────────────
#
# Added by @GCW: Senior QA Engineer during independent audit of the
# integration layer. These tests target gaps identified in the audit:
# idempotency across versions, content drift, partial deploy maps,
# multi-target, dry-run safety, corrupted config, permission errors,
# prompt-mode deployment, and CLI paths not covered by the original suite.


class TestStampingEdgeCases:
    """Stamping edge cases not covered by the original TestStamping."""

    def test_stamp_empty_file(self) -> None:
        """An empty file should still receive a stamp without error."""
        stamped = stamp_content("", "1.2.0")
        assert read_stamp(stamped) == "1.2.0"

    def test_stamp_whitespace_only_file(self) -> None:
        """A file with only whitespace should be stamped cleanly."""
        stamped = stamp_content("   \n\n", "1.2.0")
        assert read_stamp(stamped) == "1.2.0"

    def test_stamp_frontmatter_only_file(self) -> None:
        """A file containing only front-matter (no body) should be stamped."""
        content = "---\napplyTo: '**'\n---\n"
        stamped = stamp_content(content, "1.2.0")
        assert read_stamp(stamped) == "1.2.0"

    def test_stamp_preserves_shebang_and_frontmatter(self) -> None:
        """A file with both shebang and front-matter stamps after both."""
        content = "#!/bin/bash\n---\nkey: value\n---\n# body\n"
        stamped = stamp_content(content, "1.2.0")
        lines = stamped.splitlines()
        assert lines[0] == "#!/bin/bash"
        # Stamp must come after the closing front-matter delimiter.
        stamp_idx = next(i for i, line in enumerate(lines) if "mnemos-integration" in line)
        fm_close_idx = max(i for i, line in enumerate(lines) if line.strip() == "---")
        assert stamp_idx > fm_close_idx

    def test_read_stamp_from_multiline_content(self) -> None:
        """Stamp can be read from content where it's not on the first line."""
        content = "#!/bin/bash\n<!-- mnemos-integration: v0.9.0 -->\necho hi\n"
        assert read_stamp(content) == "0.9.0"

    def test_make_stamp_different_versions(self) -> None:
        """Stamp reflects the exact version string passed."""
        assert make_stamp("0.1.0") != make_stamp("1.0.0")
        assert "v0.1.0" in make_stamp("0.1.0")


class TestTargetsConfigEdgeCases:
    """Edge cases for targets.yaml parsing."""

    def test_load_targets_yaml_syntax_error(self, tmp_path: Path) -> None:
        """Malformed YAML (syntax error) should raise, not silently parse."""
        bad = tmp_path / "broken.yaml"
        bad.write_text("targets: [unclosed", encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            load_targets(bad)

    def test_load_targets_empty_targets_dict(self, tmp_path: Path) -> None:
        """An empty targets dict should parse to zero targets, not error."""
        cfg_file = tmp_path / "empty.yaml"
        cfg_file.write_text("targets: {}\n", encoding="utf-8")
        cfg = load_targets(cfg_file)
        assert len(cfg.targets) == 0
        assert cfg.detected() == ()

    def test_load_targets_target_missing_detect_key(self, tmp_path: Path) -> None:
        """A target without 'detect' should parse with empty detect_paths."""
        cfg_file = tmp_path / "no_detect.yaml"
        cfg_file.write_text(
            yaml.dump(
                {
                    "targets": {
                        "bare": {"deploy": {"instructions": str(tmp_path / "out") + "/"}},
                    }
                }
            ),
            encoding="utf-8",
        )
        cfg = load_targets(cfg_file)
        assert len(cfg.targets) == 1
        assert cfg.targets[0].detect_paths == ()

    def test_load_targets_detect_entry_missing_path_key(self, tmp_path: Path) -> None:
        """A detect entry without 'path' is silently skipped (not crash)."""
        cfg_file = tmp_path / "bad_detect.yaml"
        cfg_file.write_text(
            yaml.dump(
                {
                    "targets": {
                        "t": {
                            "detect": [{"not_path": "x"}],
                            "deploy": {"instructions": str(tmp_path / "o") + "/"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        cfg = load_targets(cfg_file)
        assert cfg.targets[0].detect_paths == ()

    def test_load_targets_deploy_non_string_value(self, tmp_path: Path) -> None:
        """Non-string deploy values are silently skipped."""
        cfg_file = tmp_path / "bad_deploy.yaml"
        cfg_file.write_text(
            yaml.dump(
                {
                    "targets": {
                        "t": {
                            "detect": [{"path": str(tmp_path / "m")}],
                            "deploy": {"instructions": 12345},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        cfg = load_targets(cfg_file)
        assert "instructions" not in cfg.targets[0].deploy_map

    def test_load_targets_target_spec_not_dict(self, tmp_path: Path) -> None:
        """A target spec that is not a mapping should raise ValueError."""
        cfg_file = tmp_path / "bad_spec.yaml"
        cfg_file.write_text(
            yaml.dump({"targets": {"bad": "just-a-string"}}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="must be a mapping"):
            load_targets(cfg_file)

    def test_load_targets_detect_not_list(self, tmp_path: Path) -> None:
        """A 'detect' that is not a list should raise ValueError."""
        cfg_file = tmp_path / "bad_detect_type.yaml"
        cfg_file.write_text(
            yaml.dump(
                {
                    "targets": {
                        "t": {
                            "detect": "not-a-list",
                            "deploy": {"instructions": str(tmp_path / "o") + "/"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="detect must be a list"):
            load_targets(cfg_file)

    def test_load_targets_deploy_not_mapping(self, tmp_path: Path) -> None:
        """A 'deploy' that is not a mapping should raise ValueError."""
        cfg_file = tmp_path / "bad_deploy_type.yaml"
        cfg_file.write_text(
            yaml.dump(
                {
                    "targets": {
                        "t": {
                            "detect": [{"path": str(tmp_path / "m")}],
                            "deploy": "not-a-mapping",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="deploy must be a mapping"):
            load_targets(cfg_file)

    def test_get_returns_none_for_unknown(self, fake_pack: Path) -> None:
        """TargetsConfig.get returns None for an unknown target name."""
        cfg = load_targets(fake_pack / "targets.yaml")
        assert cfg.get("does-not-exist") is None

    def test_detected_returns_only_detected(self, fake_pack: Path) -> None:
        """detected() filters out targets whose detect paths don't exist."""
        cfg = load_targets(fake_pack / "targets.yaml")
        detected = cfg.detected()
        assert all(t.is_detected() for t in detected)


class TestDeployEdgeCases:
    """Deploy edge cases: content drift, partial maps, multi-target."""

    def test_deploy_content_drift_same_version(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """If the pack content changes but version stays the same, deploy
        should detect the drift and mark the file as UPDATED, not CURRENT."""
        manager.deploy(detected_target)

        # Mutate the source pack file (same version, different content).
        instr_file = manager.pack_root / "instructions" / "mnemos-memory.instructions.md"
        original = instr_file.read_text(encoding="utf-8")
        instr_file.write_text(
            original + "\n# Extra line added after initial deploy\n",
            encoding="utf-8",
        )

        result = manager.deploy(detected_target)
        # The instructions file should be UPDATED (content changed).
        instr_result = next(f for f in result.files if "mnemos-memory" in f.source.name)
        assert instr_result.status == DeployStatus.UPDATED

    def test_deploy_partial_deploy_map_skips_unmapped_kinds(self, tmp_path: Path) -> None:
        """A target that only has 'instructions' in its deploy map should
        silently skip skills and prompts — no noisy SKIPPED rows.

        Not every target supports every artefact kind (e.g. generic-copilot
        only has prompts, copilot has instructions+skills). Unsupported kinds
        are skipped silently with a debug log, not reported as SKIPPED in
        the result (which made users think something was broken).
        """
        pack = tmp_path / "integrations"
        (pack / "instructions").mkdir(parents=True)
        (pack / "skills").mkdir(parents=True)
        (pack / "prompts").mkdir(parents=True)
        (pack / "instructions" / "a.md").write_text("# a\n", encoding="utf-8")
        (pack / "skills" / "b.md").write_text("# b\n", encoding="utf-8")
        (pack / "prompts" / "c.md").write_text("# c\n", encoding="utf-8")

        marker = tmp_path / "marker"
        marker.mkdir()
        (pack / "targets.yaml").write_text(
            yaml.dump(
                {
                    "targets": {
                        "partial": {
                            "detect": [{"path": str(marker)}],
                            "deploy": {
                                "instructions": str(tmp_path / "deploy" / "instr") + "/",
                            },
                            "format": "copy",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        cfg = load_targets(pack / "targets.yaml")
        mgr = IntegrationManager(version="1.0.0", pack_root=pack, targets_config=cfg)

        result = mgr.deploy("partial")
        # Only the instructions file is in the result — skills+prompts are
        # silently skipped (no deploy map for those kinds).
        assert len(result.files) == 1
        statuses = {f.source.name: f.status for f in result.files}
        assert statuses["a.md"] == DeployStatus.DEPLOYED
        # b.md and c.md are NOT in the result at all — silent skip.
        assert "b.md" not in statuses
        assert "c.md" not in statuses

    def test_deploy_multi_target_all_detected(self, tmp_path: Path) -> None:
        """Deploy to multiple detected targets — all should receive files."""
        pack = tmp_path / "integrations"
        (pack / "instructions").mkdir(parents=True)
        (pack / "instructions" / "shared.md").write_text("# shared\n", encoding="utf-8")

        marker_a = tmp_path / "marker_a"
        marker_b = tmp_path / "marker_b"
        marker_a.mkdir()
        marker_b.mkdir()

        deploy_a = tmp_path / "deploy_a" / "instructions"
        deploy_b = tmp_path / "deploy_b" / "instructions"

        (pack / "targets.yaml").write_text(
            yaml.dump(
                {
                    "targets": {
                        "target-a": {
                            "detect": [{"path": str(marker_a)}],
                            "deploy": {"instructions": str(deploy_a) + "/"},
                            "format": "copy",
                        },
                        "target-b": {
                            "detect": [{"path": str(marker_b)}],
                            "deploy": {"instructions": str(deploy_b) + "/"},
                            "format": "copy",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        cfg = load_targets(pack / "targets.yaml")
        mgr = IntegrationManager(version="2.0.0", pack_root=pack, targets_config=cfg)

        assert len(cfg.detected()) == 2

        for name in ("target-a", "target-b"):
            result = mgr.deploy(name)
            assert result.deployed_count == 1

        assert (deploy_a / "shared.md").exists()
        assert (deploy_b / "shared.md").exists()
        assert read_stamp((deploy_a / "shared.md").read_text()) == "2.0.0"
        assert read_stamp((deploy_b / "shared.md").read_text()) == "2.0.0"

    def test_deploy_preserves_user_file_in_deploy_dir(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """A pre-existing user file in the deploy dir is not overwritten
        even if it has the same name as a pack file — deploy writes to the
        correct relative path and leaves unrelated user files alone."""
        cfg = load_targets(manager.pack_root / "targets.yaml")
        dest_dir = cfg.targets[0].deploy_map["instructions"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        user_file = dest_dir / "my-own-instruction.md"
        user_file.write_text("# mine, not mnemos\n", encoding="utf-8")

        manager.deploy(detected_target)
        # User file content unchanged.
        assert "mine, not mnemos" in user_file.read_text(encoding="utf-8")
        assert read_stamp(user_file.read_text()) is None

    def test_deploy_unknown_target_raises_value_error(self, manager: IntegrationManager) -> None:
        """Deploying to an unknown target raises ValueError with the name."""
        with pytest.raises(ValueError, match="Unknown target"):
            manager.deploy("totally-fake-target")

    def test_deploy_creates_nested_deploy_dirs(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """Deploy creates deeply nested parent directories as needed."""
        manager.deploy(detected_target)
        cfg = load_targets(manager.pack_root / "targets.yaml")
        # skills/mnemos-recall/SKILL.md — nested 2 levels under deploy root.
        nested = cfg.targets[0].deploy_map["skills"] / "mnemos-recall" / "SKILL.md"
        assert nested.exists()


class TestVerifyEdgeCases:
    """Verify edge cases: stale-removed-from-pack, empty deploy dir."""

    def test_verify_detects_stamped_file_removed_from_pack(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """A stamped file that was deployed but later removed from the pack
        should be reported as STALE (not SKIPPED), because it carries our
        stamp and is no longer managed."""
        manager.deploy(detected_target)

        # Remove a file from the pack.
        removed = manager.pack_root / "instructions" / "mnemos-memory.instructions.md"
        removed.unlink()

        result = manager.verify(detected_target)
        # The deployed copy still exists and is stamped but not in pack.
        stale_extras = [
            f
            for f in result.files
            if f.status == DeployStatus.STALE and f.source == Path("<not-in-pack>")
        ]
        assert len(stale_extras) == 1

    def test_verify_empty_deploy_dir(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """Verify on an empty (but existing) deploy dir reports all missing."""
        cfg = load_targets(manager.pack_root / "targets.yaml")
        for d in cfg.targets[0].deploy_map.values():
            d.mkdir(parents=True, exist_ok=True)

        result = manager.verify(detected_target)
        assert result.missing_count == 3
        assert not result.all_current

    def test_verify_unknown_target_raises(self, manager: IntegrationManager) -> None:
        with pytest.raises(ValueError, match="Unknown target"):
            manager.verify("nope")

    def test_verify_all_current_property_false_when_no_files(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """all_current is False when there are zero files (vacuous truth guard)."""
        # Remove all pack files so _all_pack_files returns empty.
        import shutil

        for kind in ("instructions", "skills", "prompts"):
            d = manager.pack_root / kind
            if d.exists():
                shutil.rmtree(d)

        result = manager.verify(detected_target)
        assert len(result.files) == 0
        assert not result.all_current


class TestUpdateEdgeCases:
    """Update edge cases: missing files deployed, idempotency."""

    def test_update_deploys_missing_files(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """Update should deploy files that are missing entirely, not just
        update stale ones."""
        result = manager.update(detected_target)
        assert result.deployed_count == 3
        verify = manager.verify(detected_target)
        assert verify.all_current

    def test_update_idempotent_second_run(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """Running update twice doesn't change files on the second run."""
        manager.update(detected_target)
        result = manager.update(detected_target)
        assert all(f.status == DeployStatus.CURRENT for f in result.files)
        assert result.deployed_count == 0

    def test_update_unknown_target_raises(self, manager: IntegrationManager) -> None:
        with pytest.raises(ValueError, match="Unknown target"):
            manager.update("ghost")

    def test_update_dry_run_preserves_old_stamp(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """Dry-run update leaves the old version stamp intact on disk."""
        old_mgr = IntegrationManager(
            version="0.5.0",
            pack_root=manager.pack_root,
            targets_config=manager.targets,
        )
        old_mgr.deploy(detected_target)

        manager.update(detected_target, dry_run=True)
        # Files still at old version.
        verify = manager.verify(detected_target)
        assert verify.stale_count == 3


class TestUninstallEdgeCases:
    """Uninstall edge cases: unknown target, nested dirs, mixed content."""

    def test_uninstall_unknown_target_raises(self, manager: IntegrationManager) -> None:
        with pytest.raises(ValueError, match="Unknown target"):
            manager.uninstall("phantom")

    def test_uninstall_preserves_nested_user_files(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """User files in nested subdirectories are preserved during uninstall."""
        manager.deploy(detected_target)
        cfg = load_targets(manager.pack_root / "targets.yaml")
        skills_dir = cfg.targets[0].deploy_map["skills"] / "user-skill"
        skills_dir.mkdir(parents=True, exist_ok=True)
        user_skill = skills_dir / "SKILL.md"
        user_skill.write_text("# my custom skill\n", encoding="utf-8")

        manager.uninstall(detected_target)
        assert user_skill.exists()
        assert skills_dir.exists()  # not cleaned because user file remains

    def test_uninstall_when_deploy_dir_missing(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """Uninstall on a target whose deploy dirs don't exist returns empty."""
        result = manager.uninstall(detected_target)
        assert len(result.removed) == 0
        assert len(result.skipped_user_files) == 0

    def test_uninstall_mixed_stamped_and_user(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """Uninstall with a mix of stamped and user files in the same dir."""
        manager.deploy(detected_target)
        cfg = load_targets(manager.pack_root / "targets.yaml")
        dest_dir = cfg.targets[0].deploy_map["instructions"]
        user_a = dest_dir / "user-a.md"
        user_b = dest_dir / "user-b.md"
        user_a.write_text("# a\n", encoding="utf-8")
        user_b.write_text("# b\n", encoding="utf-8")

        result = manager.uninstall(detected_target)
        assert len(result.removed) == 3  # the 3 pack files
        assert user_a.exists()
        assert user_b.exists()
        assert len(result.skipped_user_files) == 2

    def test_uninstall_dry_run_reports_but_preserves(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """Dry-run uninstall reports what would be removed but leaves files."""
        manager.deploy(detected_target)
        result = manager.uninstall(detected_target, dry_run=True)
        assert len(result.removed) == 3
        for p in result.removed:
            assert p.exists()


class TestSetupMCP:
    """MCP registration paths in IntegrationManager.setup."""

    def test_setup_skips_mcp_when_disabled(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """setup with register_mcp=False should not attempt MCP registration."""
        result = manager.setup(detected_target, register_mcp=False)
        assert result.mcp_registered is False
        assert result.mcp_note == ""
        assert result.deployed_count == 3

    def test_setup_dry_run_skips_mcp(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """Dry-run setup should not register MCP even if register_mcp=True."""
        result = manager.setup(detected_target, dry_run=True, register_mcp=True)
        assert result.mcp_registered is False
        assert result.deployed_count == 3

    def test_setup_mcp_failure_does_not_block_deploy(
        self, manager: IntegrationManager, detected_target: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If MCP registration fails, files are still deployed; the failure
        is recorded in mcp_note."""
        monkeypatch.setattr(
            IntegrationManager, "_find_mcp_setup_script", staticmethod(lambda: None)
        )
        result = manager.setup(detected_target, register_mcp=True, mnemos_bin="/nonexistent/mnemos")
        assert result.deployed_count == 3
        assert result.mcp_registered is False
        assert result.mcp_note != ""

    def test_register_mcp_missing_script(
        self, manager: IntegrationManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """register_mcp returns (False, note) when mcp-setup.sh is absent."""
        monkeypatch.setattr(
            IntegrationManager, "_find_mcp_setup_script", staticmethod(lambda: None)
        )
        ok, note = manager.register_mcp()
        assert ok is False
        assert "mcp-setup.sh" in note


class TestFindMcpSetupScript:
    """_find_mcp_setup_script() 3-tier lookup for mcp-setup.sh."""

    def test_returns_path_in_source_tree(self) -> None:
        """In the repo source-tree layout, the helper finds scripts/mcp-setup.sh."""
        script = IntegrationManager._find_mcp_setup_script()
        # In the test environment (running from source checkout), the script
        # must be found — either via source-tree layout or upward search.
        assert script is not None
        assert script.name == "mcp-setup.sh"

    def test_returned_path_exists_and_is_file(self) -> None:
        """The path returned by the helper must point to an existing file."""
        script = IntegrationManager._find_mcp_setup_script()
        assert script is not None
        assert script.is_file()

    def test_returns_none_when_no_scripts_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no scripts/ exists anywhere, the helper returns None.

        We simulate this by placing __file__ in a deep tmp_path with no
        scripts/ sibling and monkeypatching importlib.resources to miss.
        """
        # Build a fake package layout: tmp_path/src/mnemos/cli/integration.py
        fake_cli = tmp_path / "src" / "mnemos" / "cli"
        fake_cli.mkdir(parents=True)
        fake_module = fake_cli / "integration.py"
        fake_module.write_text("# fake\n")

        # Monkeypatch __file__ inside the integration module so the helper
        # resolves relative to our fake location.
        import mnemos.cli.integration as mod

        monkeypatch.setattr(mod, "__file__", str(fake_module))

        # Also neutralise importlib.resources so the wheel-layout branch misses.
        import importlib.resources as ilr

        class _FakeFiles:
            def __truediv__(self, other: str) -> _FakeFiles:
                return self

            def is_file(self) -> bool:
                return False

            def is_dir(self) -> bool:
                return False

        monkeypatch.setattr(ilr, "files", lambda _pkg: _FakeFiles())

        script = IntegrationManager._find_mcp_setup_script()
        assert script is None

    def test_wheel_layout_via_importlib_resources(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The importlib.resources branch finds mcp-setup.sh when present.

        We simulate a wheel install by placing __file__ deep in site-packages
        (so the source-tree branch misses) and making importlib.resources.files
        return a path to a fake scripts/mcp-setup.sh.
        """
        fake_scripts = tmp_path / "site-packages" / "mnemos" / "scripts"
        fake_scripts.mkdir(parents=True)
        fake_script = fake_scripts / "mcp-setup.sh"
        fake_script.write_text("#!/bin/bash\n# fake mcp-setup\n")

        fake_cli = tmp_path / "site-packages" / "mnemos" / "cli"
        fake_cli.mkdir(parents=True)
        fake_module = fake_cli / "integration.py"
        fake_module.write_text("# fake\n")

        import mnemos.cli.integration as mod

        monkeypatch.setattr(mod, "__file__", str(fake_module))

        import importlib.resources as ilr

        fake_pkg_root = tmp_path / "site-packages" / "mnemos"

        class _FakeTraversable:
            def __init__(self, path: Path) -> None:
                self._path = path

            def __truediv__(self, other: str) -> _FakeTraversable:
                return _FakeTraversable(self._path / other)

            def __str__(self) -> str:
                return str(self._path)

            def is_file(self) -> bool:
                return self._path.is_file()

            def is_dir(self) -> bool:
                return self._path.is_dir()

        monkeypatch.setattr(ilr, "files", lambda _pkg: _FakeTraversable(fake_pkg_root))

        script = IntegrationManager._find_mcp_setup_script()
        assert script is not None
        assert script.is_file()
        assert script.name == "mcp-setup.sh"


class TestCLISetupUpdateUninstall:
    """CLI paths for setup, update, and uninstall not covered by original suite."""

    def test_integration_setup_dry_run_via_cli(
        self,
        fake_pack: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CLI `integration setup --dry-run` should not write files."""
        cfg = load_targets(fake_pack / "targets.yaml")
        mgr = IntegrationManager(version="1.2.0", pack_root=fake_pack, targets_config=cfg)

        import mnemos.cli.util as util_mod

        monkeypatch.setattr(util_mod, "_manager", lambda pack_root=None: mgr)
        monkeypatch.setattr(util_mod, "load_targets", lambda config_path=None: cfg)

        result = runner.invoke(
            app, ["integration", "setup", "--target", "test-harness", "--dry-run", "--no-mcp"]
        )
        assert result.exit_code == 0
        # No files written.
        for kind in ("instructions", "skills", "prompts"):
            deploy_dir = cfg.targets[0].deploy_map.get(kind)
            if deploy_dir:
                assert not deploy_dir.exists() or not any(deploy_dir.iterdir())

    def test_integration_setup_writes_files_via_cli(
        self,
        fake_pack: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CLI `integration setup` (non-dry-run) deploys files to disk."""
        cfg = load_targets(fake_pack / "targets.yaml")
        mgr = IntegrationManager(version="1.2.0", pack_root=fake_pack, targets_config=cfg)

        import mnemos.cli.util as util_mod

        monkeypatch.setattr(util_mod, "_manager", lambda pack_root=None: mgr)
        monkeypatch.setattr(util_mod, "load_targets", lambda config_path=None: cfg)

        result = runner.invoke(
            app, ["integration", "setup", "--target", "test-harness", "--no-mcp"]
        )
        assert result.exit_code == 0
        assert "Setup complete" in result.output

    def test_integration_update_via_cli(
        self,
        fake_pack: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CLI `integration update` brings stale files to current."""
        cfg = load_targets(fake_pack / "targets.yaml")
        old_mgr = IntegrationManager(version="0.1.0", pack_root=fake_pack, targets_config=cfg)
        old_mgr.deploy("test-harness")

        new_mgr = IntegrationManager(version="9.9.9", pack_root=fake_pack, targets_config=cfg)

        import mnemos.cli.util as util_mod

        monkeypatch.setattr(util_mod, "_manager", lambda pack_root=None: new_mgr)
        monkeypatch.setattr(util_mod, "load_targets", lambda config_path=None: cfg)

        result = runner.invoke(app, ["integration", "update", "--target", "test-harness"])
        assert result.exit_code == 0
        assert "Update complete" in result.output

    def test_integration_uninstall_via_cli(
        self,
        fake_pack: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CLI `integration uninstall` removes stamped files."""
        cfg = load_targets(fake_pack / "targets.yaml")
        mgr = IntegrationManager(version="1.2.0", pack_root=fake_pack, targets_config=cfg)
        mgr.deploy("test-harness")

        import mnemos.cli.util as util_mod

        monkeypatch.setattr(util_mod, "_manager", lambda pack_root=None: mgr)
        monkeypatch.setattr(util_mod, "load_targets", lambda config_path=None: cfg)

        result = runner.invoke(app, ["integration", "uninstall", "--target", "test-harness"])
        assert result.exit_code == 0
        assert "Uninstall complete" in result.output
        assert "3 files removed" in result.output

    def test_integration_uninstall_dry_run_via_cli(
        self,
        fake_pack: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CLI `integration uninstall --dry-run` reports but doesn't delete."""
        cfg = load_targets(fake_pack / "targets.yaml")
        mgr = IntegrationManager(version="1.2.0", pack_root=fake_pack, targets_config=cfg)
        mgr.deploy("test-harness")

        import mnemos.cli.util as util_mod

        monkeypatch.setattr(util_mod, "_manager", lambda pack_root=None: mgr)
        monkeypatch.setattr(util_mod, "load_targets", lambda config_path=None: cfg)

        result = runner.invoke(
            app, ["integration", "uninstall", "--target", "test-harness", "--dry-run"]
        )
        assert result.exit_code == 0
        assert "3 files removed" in result.output

    def test_integration_setup_all_targets_no_detection(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CLI `integration setup --target all` with no detected harnesses exits 0
        and prints a 'no harnesses' message."""
        import mnemos.cli.util as util_mod

        # Empty config with no detected targets.
        empty_cfg = TargetsConfig(targets=())
        monkeypatch.setattr(util_mod, "load_targets", lambda config_path=None: empty_cfg)

        result = runner.invoke(app, ["integration", "setup", "--target", "all"])
        assert result.exit_code == 0
        assert "No agent harnesses detected" in result.output

    def test_integration_verify_all_targets_no_detection(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CLI `integration verify --target all` with no detected harnesses exits 0."""
        import mnemos.cli.util as util_mod

        empty_cfg = TargetsConfig(targets=())
        monkeypatch.setattr(util_mod, "load_targets", lambda config_path=None: empty_cfg)

        result = runner.invoke(app, ["integration", "verify", "--target", "all"])
        assert result.exit_code == 0

    def test_integration_detect_no_harnesses(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CLI `integration detect` with no detected harnesses prints a message."""
        import mnemos.cli.util as util_mod

        empty_cfg = TargetsConfig(targets=())
        monkeypatch.setattr(util_mod, "load_targets", lambda config_path=None: empty_cfg)

        result = runner.invoke(app, ["integration", "detect"])
        assert result.exit_code == 0
        assert "No agent harnesses detected" in result.output

    def test_integration_setup_specific_undetected_target(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """CLI `integration setup --target X` where X exists in config but is not
        detected should exit 0 with a 'not detected' warning."""
        import mnemos.cli.util as util_mod

        cfg = TargetsConfig(
            targets=(
                Target(
                    name="ghost",
                    detect_paths=(tmp_path / "nonexistent",),
                    deploy_map={"instructions": tmp_path / "out"},
                ),
            )
        )
        monkeypatch.setattr(util_mod, "load_targets", lambda config_path=None: cfg)

        result = runner.invoke(app, ["integration", "setup", "--target", "ghost"])
        assert result.exit_code == 0
        assert "not detected" in result.output.lower()


class TestFullLifecycleMultiVersion:
    """Full lifecycle across multiple version transitions."""

    def test_version_progression_deploy_update_verify(self, fake_pack: Path) -> None:
        """Simulate: deploy v1.0.0 → verify stale at v1.1.0 → update →
        verify current → deploy v1.2.0 → update → verify current → uninstall."""
        cfg = load_targets(fake_pack / "targets.yaml")

        v1 = IntegrationManager(version="1.0.0", pack_root=fake_pack, targets_config=cfg)
        v1.deploy("test-harness")

        v11 = IntegrationManager(version="1.1.0", pack_root=fake_pack, targets_config=cfg)
        assert v11.verify("test-harness").stale_count == 3
        v11.update("test-harness")
        assert v11.verify("test-harness").all_current

        v12 = IntegrationManager(version="1.2.0", pack_root=fake_pack, targets_config=cfg)
        v12.update("test-harness")
        assert v12.verify("test-harness").all_current

        uninstall = v12.uninstall("test-harness")
        assert len(uninstall.removed) == 3
        assert v12.verify("test-harness").missing_count == 3

    def test_repeated_deploy_same_version_no_duplicates(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """Deploying the same version 5 times should not create duplicate
        files or change file count."""
        for _ in range(5):
            manager.deploy(detected_target)

        cfg = load_targets(manager.pack_root / "targets.yaml")
        instr_dir = cfg.targets[0].deploy_map["instructions"]
        # Exactly one .md file in instructions (the pack file).
        md_files = list(instr_dir.rglob("*.md"))
        assert len(md_files) == 1

    def test_deploy_update_uninstall_preserves_user_files_throughout(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """User files survive a full deploy → update → uninstall cycle."""
        cfg = load_targets(manager.pack_root / "targets.yaml")
        dest_dir = cfg.targets[0].deploy_map["instructions"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        user_file = dest_dir / "persistent-user.md"
        user_file.write_text("# persistent\n", encoding="utf-8")

        manager.deploy(detected_target)
        assert user_file.exists()

        old_mgr = IntegrationManager(
            version="0.1.0",
            pack_root=manager.pack_root,
            targets_config=manager.targets,
        )
        old_mgr.deploy(detected_target)
        manager.update(detected_target)
        assert user_file.exists()
        assert "persistent" in user_file.read_text()

        manager.uninstall(detected_target)
        assert user_file.exists()
        assert "persistent" in user_file.read_text()


class TestPermissionErrors:
    """Graceful handling of permission errors on deploy directories."""

    def test_deploy_permission_error_raises(
        self,
        fake_pack: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the deploy directory is not writable, deploy should raise
        PermissionError (or OSError), not silently swallow the error."""
        import os

        cfg = load_targets(fake_pack / "targets.yaml")
        # Point deploy to a read-only directory.
        ro_dir = tmp_path / "readonly" / "instructions"
        ro_dir.mkdir(parents=True)
        ro_dir.chmod(0o444)

        # Rebuild config with the read-only deploy path.
        (fake_pack / "targets.yaml").write_text(
            yaml.dump(
                {
                    "targets": {
                        "test-harness": {
                            "detect": [{"path": str(tmp_path / "harness-marker")}],
                            "deploy": {
                                "instructions": str(ro_dir) + "/",
                                "skills": str(tmp_path / "rw" / "skills") + "/",
                                "prompts": str(tmp_path / "rw" / "prompts") + "/",
                            },
                            "format": "copy",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        cfg = load_targets(fake_pack / "targets.yaml")
        mgr = IntegrationManager(version="1.0.0", pack_root=fake_pack, targets_config=cfg)

        if os.geteuid() == 0:
            pytest.skip("running as root — permission test is meaningless")
        with pytest.raises((PermissionError, OSError)):
            mgr.deploy("test-harness")

        # Cleanup so tmp_path teardown doesn't fail.
        ro_dir.chmod(0o755)


class TestPromptModeDeployment:
    """Verify prompt files deploy to the correct directory."""

    def test_prompt_deploys_to_prompts_dir(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """Prompt files land in the 'prompts' deploy directory, not instructions."""
        manager.deploy(detected_target)
        cfg = load_targets(manager.pack_root / "targets.yaml")
        prompts_dir = cfg.targets[0].deploy_map["prompts"]
        prompt_file = prompts_dir / "mnemos-session.prompt.md"
        assert prompt_file.exists()
        assert read_stamp(prompt_file.read_text()) == "1.2.0"

    def test_prompt_file_not_in_instructions_dir(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """Prompt files must NOT appear in the instructions deploy dir."""
        manager.deploy(detected_target)
        cfg = load_targets(manager.pack_root / "targets.yaml")
        instr_dir = cfg.targets[0].deploy_map["instructions"]
        assert not (instr_dir / "mnemos-session.prompt.md").exists()

    def test_prompt_uninstall_removes_only_prompts(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """Uninstall removes prompt files from the prompts dir."""
        manager.deploy(detected_target)
        cfg = load_targets(manager.pack_root / "targets.yaml")
        prompts_dir = cfg.targets[0].deploy_map["prompts"]
        prompt_file = prompts_dir / "mnemos-session.prompt.md"
        assert prompt_file.exists()

        manager.uninstall(detected_target)
        assert not prompt_file.exists()


class TestVersionStampFormat:
    """Verify the stamp format is consistent and parseable across file types."""

    def test_stamp_format_regex_matches(self) -> None:
        """The stamp matches the STAMP_PATTERN regex."""
        from mnemos.cli.integration import STAMP_PATTERN

        stamp = make_stamp("1.2.3")
        match = STAMP_PATTERN.search(stamp)
        assert match is not None
        assert match.group(1) == "1.2.3"

    def test_stamp_applied_to_yaml_like_content(self) -> None:
        """Stamping works on content that looks like YAML (but is markdown)."""
        content = "---\nkey: value\n---\n# doc\n"
        stamped = stamp_content(content, "1.0.0")
        assert read_stamp(stamped) == "1.0.0"

    def test_stamp_version_with_pre_release_suffix(self) -> None:
        """Pre-release versions (e.g. 1.0.0-rc1) are handled correctly."""
        stamped = stamp_content("# title\n", "1.0.0-rc1")
        assert read_stamp(stamped) == "1.0.0-rc1"

    def test_stamp_version_consistent_across_redeploy(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """The stamp version is identical after multiple deploys."""
        manager.deploy(detected_target)
        cfg = load_targets(manager.pack_root / "targets.yaml")
        instr_file = cfg.targets[0].deploy_map["instructions"] / "mnemos-memory.instructions.md"
        v1 = read_stamp(instr_file.read_text())

        manager.deploy(detected_target)
        v2 = read_stamp(instr_file.read_text())

        assert v1 == v2 == manager.version

    def test_stamp_not_in_frontmatter_block(
        self, manager: IntegrationManager, detected_target: str
    ) -> None:
        """The stamp must not appear inside the YAML front-matter block."""
        manager.deploy(detected_target)
        cfg = load_targets(manager.pack_root / "targets.yaml")
        instr_file = cfg.targets[0].deploy_map["instructions"] / "mnemos-memory.instructions.md"
        content = instr_file.read_text(encoding="utf-8")
        lines = content.splitlines()

        # Find front-matter boundaries.
        fm_lines = [i for i, line in enumerate(lines) if line.strip() == "---"]
        stamp_line = next((i for i, line in enumerate(lines) if "mnemos-integration" in line), None)
        assert stamp_line is not None
        # If there are 2+ '---' lines, stamp must be after the last one.
        if len(fm_lines) >= 2:
            assert stamp_line > fm_lines[-1]


class TestCorruptedConfig:
    """Graceful handling of corrupted or malformed targets.yaml."""

    def test_empty_yaml_file(self, tmp_path: Path) -> None:
        """An empty YAML file (None after parse) should raise ValueError."""
        cfg_file = tmp_path / "empty.yaml"
        cfg_file.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="expected top-level 'targets'"):
            load_targets(cfg_file)

    def test_yaml_null_content(self, tmp_path: Path) -> None:
        """A YAML file with just 'null' should raise ValueError."""
        cfg_file = tmp_path / "null.yaml"
        cfg_file.write_text("null\n", encoding="utf-8")
        with pytest.raises(ValueError, match="expected top-level 'targets'"):
            load_targets(cfg_file)

    def test_targets_key_not_a_dict(self, tmp_path: Path) -> None:
        """When 'targets' is a list instead of a mapping, raise ValueError."""
        cfg_file = tmp_path / "list.yaml"
        cfg_file.write_text("targets: [a, b]\n", encoding="utf-8")
        with pytest.raises(ValueError, match="'targets' must be a mapping"):
            load_targets(cfg_file)


class TestMissingIntegrationsDir:
    """Graceful handling when integrations/ is empty or missing."""

    def test_pack_root_missing_all_dirs(self, tmp_path: Path) -> None:
        """When the pack root has no artefact subdirs, deploy produces zero
        files and verify reports zero missing."""
        pack = tmp_path / "empty-pack"
        pack.mkdir()
        (pack / "targets.yaml").write_text(
            yaml.dump(
                {
                    "targets": {
                        "t": {
                            "detect": [{"path": str(tmp_path / "m")}],
                            "deploy": {"instructions": str(tmp_path / "o") + "/"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        cfg = load_targets(pack / "targets.yaml")
        mgr = IntegrationManager(version="1.0.0", pack_root=pack, targets_config=cfg)

        result = mgr.deploy("t")
        assert len(result.files) == 0
        assert result.deployed_count == 0

        verify = mgr.verify("t")
        assert len(verify.files) == 0

    def test_pack_root_with_empty_subdirs(self, tmp_path: Path) -> None:
        """When artefact subdirs exist but are empty, deploy produces zero files."""
        pack = tmp_path / "pack"
        for kind in ("instructions", "skills", "prompts"):
            (pack / kind).mkdir(parents=True)
        (pack / "targets.yaml").write_text(
            yaml.dump(
                {
                    "targets": {
                        "t": {
                            "detect": [{"path": str(tmp_path / "m")}],
                            "deploy": {
                                "instructions": str(tmp_path / "o1") + "/",
                                "skills": str(tmp_path / "o2") + "/",
                                "prompts": str(tmp_path / "o3") + "/",
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        cfg = load_targets(pack / "targets.yaml")
        mgr = IntegrationManager(version="1.0.0", pack_root=pack, targets_config=cfg)

        result = mgr.deploy("t")
        assert result.deployed_count == 0

    def test_pack_files_with_non_deployable_suffix_skipped(self, tmp_path: Path) -> None:
        """Files with non-deployable suffixes (.gitkeep, .py, etc.) are skipped."""
        pack = tmp_path / "pack"
        instr = pack / "instructions"
        instr.mkdir(parents=True)
        (instr / "valid.md").write_text("# valid\n", encoding="utf-8")
        (instr / ".gitkeep").write_text("", encoding="utf-8")
        (instr / "script.py").write_text("print('hi')\n", encoding="utf-8")
        (pack / "targets.yaml").write_text(
            yaml.dump(
                {
                    "targets": {
                        "t": {
                            "detect": [{"path": str(tmp_path / "m")}],
                            "deploy": {"instructions": str(tmp_path / "o") + "/"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        cfg = load_targets(pack / "targets.yaml")
        mgr = IntegrationManager(version="1.0.0", pack_root=pack, targets_config=cfg)

        result = mgr.deploy("t")
        deployed_names = [f.source.name for f in result.files if f.status == DeployStatus.DEPLOYED]
        assert "valid.md" in deployed_names
        assert ".gitkeep" not in deployed_names
        assert "script.py" not in deployed_names


class TestDetectAllAndDeployableTargets:
    """Cover the module-level convenience functions."""

    def test_detect_all_returns_list(self, fake_pack: Path) -> None:
        from mnemos.cli.integration import detect_all

        cfg = load_targets(fake_pack / "targets.yaml")
        detected = detect_all(cfg)
        assert isinstance(detected, list)
        assert len(detected) == 1
        assert detected[0].name == "test-harness"

    def test_deployable_targets_returns_all_names(self, fake_pack: Path) -> None:
        from mnemos.cli.integration import deployable_targets

        cfg = load_targets(fake_pack / "targets.yaml")
        names = deployable_targets(cfg)
        assert "test-harness" in names

    def test_detect_all_with_empty_config(self) -> None:
        from mnemos.cli.integration import detect_all

        empty = TargetsConfig(targets=())
        assert detect_all(empty) == []

    def test_deployable_targets_empty_config(self) -> None:
        from mnemos.cli.integration import deployable_targets

        empty = TargetsConfig(targets=())
        assert deployable_targets(empty) == []
