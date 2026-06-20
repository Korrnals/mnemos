"""Tests for the Mnemos integration layer (``mnemos util-*`` commands).

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
        assert "gcw" in names
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
        stamp_idx = next(
            i for i, line in enumerate(lines) if "mnemos-integration" in line
        )
        fm_end_idx = next(
            i for i, line in enumerate(lines) if line.strip() == "---" and i > 0
        )
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
    def test_util_help_exits_cleanly(self) -> None:
        result = runner.invoke(app, ["util", "--help"])
        assert result.exit_code == 0
        assert "detect" in result.output
        assert "setup" in result.output
        assert "verify" in result.output
        assert "uninstall" in result.output

    def test_util_detect_runs(self) -> None:
        result = runner.invoke(app, ["util", "detect"])
        assert result.exit_code == 0

    def test_util_setup_unknown_target(self) -> None:
        result = runner.invoke(app, ["util", "setup", "--target", "nonexistent"])
        assert result.exit_code == 1
        assert "Unknown target" in result.output

    def test_util_setup_dry_run_no_files(self, fake_pack: Path, tmp_path: Path) -> None:
        """Dry-run should not create any files."""
        cfg = load_targets(fake_pack / "targets.yaml")
        mgr = IntegrationManager(version="1.2.0", pack_root=fake_pack, targets_config=cfg)
        result = mgr.setup("test-harness", dry_run=True, register_mcp=False)
        assert result.deployed_count == 3
        # No files should exist on disk.
        for f in result.files:
            if f.status == DeployStatus.DEPLOYED:
                assert not f.destination.exists()

    def test_util_verify_exits_nonzero_when_stale(
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
        result = runner.invoke(app, ["util", "verify", "--target", detected_target])
        assert result.exit_code == 1

    def test_util_verify_exits_zero_when_current(
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

        result = runner.invoke(app, ["util", "verify", "--target", detected_target])
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
