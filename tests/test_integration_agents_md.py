"""Tests for the ``agents_md`` deployment kind and the ``opencode`` target.

Covers (issue #231 — behavioral pack for non-Copilot targets):

* Stamped BEGIN/END block engine (render / strip / read version)
* Injection into shared AGENTS.md-standard files: deploy, verify, update,
  uninstall lifecycle and idempotency
* Never-clobber guarantees: user content around the block survives every
  operation byte-for-byte; uninstall removes only the stamped block
* The ``opencode`` target: nested skills, AGENTS.md injection, and the
  additive ``opencode.json`` MCP merge (``{"type": "local", ...}``)
* Schema assertions against the REAL shipped ``targets.yaml``
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from mnemos.cli.integration import (
    DeployStatus,
    IntegrationManager,
    Target,
    TargetsConfig,
    load_targets,
    read_agents_md_version,
    read_stamp,
    render_agents_md_block,
    strip_agents_md_block,
)

VERSION = "1.2.0"
OLD_VERSION = "1.1.0"

BLOCK_BODY = "# Mnemos memory — always-on rules\n\nRecall at session start.\n"

USER_HEADER = "# My standing instructions\n\nAlways answer in English.\n"
USER_FOOTER = "\n## Project notes\n\nKeep the basement dry.\n"


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    """Fake home directory acting as the ~ root for all deploy paths."""
    return tmp_path / "home"


@pytest.fixture
def fake_pack(tmp_path: Path) -> Path:
    """Build a minimal integrations/ pack with an agents_md fragment."""
    pack = tmp_path / "integrations"
    (pack / "instructions").mkdir(parents=True)
    (pack / "skills" / "mnemos-recall").mkdir(parents=True)
    (pack / "agents_md").mkdir(parents=True)

    (pack / "instructions" / "mnemos-memory.instructions.md").write_text(
        "---\napplyTo: '**'\n---\n# Mnemos memory trigger\nUse mnemos tools.\n",
        encoding="utf-8",
    )
    (pack / "skills" / "mnemos-recall" / "SKILL.md").write_text(
        "# Mnemos recall skill\n\nRecall context from memory.\n", encoding="utf-8"
    )
    (pack / "agents_md" / "mnemos-always-on.md").write_text(BLOCK_BODY, encoding="utf-8")
    return pack


def _write_targets_yaml(pack: Path, fake_home: Path) -> None:
    """Write a targets.yaml with an agents-like and an opencode-like target."""
    (pack / "targets.yaml").write_text(
        yaml.dump(
            {
                "targets": {
                    "test-agents": {
                        "detect": [{"path": str(fake_home / ".agents")}],
                        "deploy": {
                            "skills": str(fake_home / ".agents" / "skills") + "/",
                            "agents_md": str(fake_home / ".agents" / "AGENTS.md"),
                        },
                        "format": "copy",
                        "layout": "nested",
                        "mcp": {
                            "config": str(fake_home / ".agents" / "mcp.json"),
                            "format": "agents",
                        },
                    },
                    "test-opencode": {
                        "detect": [{"path": str(fake_home / ".config" / "opencode")}],
                        "deploy": {
                            "skills": str(fake_home / ".config" / "opencode" / "skills") + "/",
                            "agents_md": str(fake_home / ".config" / "opencode" / "AGENTS.md"),
                        },
                        "format": "copy",
                        "layout": "nested",
                        "mcp": {
                            "config": str(fake_home / ".config" / "opencode" / "opencode.json"),
                            "format": "opencode",
                        },
                    },
                }
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def manager(fake_pack: Path, fake_home: Path) -> IntegrationManager:
    """Manager over the fake pack with both targets rooted in fake_home."""
    _write_targets_yaml(fake_pack, fake_home)
    cfg = load_targets(fake_pack / "targets.yaml", home=fake_home)
    return IntegrationManager(
        version=VERSION, pack_root=fake_pack, targets_config=cfg, home=fake_home
    )


@pytest.fixture
def agents_target(manager: IntegrationManager) -> Target:
    return manager.targets.get("test-agents")  # type: ignore[return-value]


@pytest.fixture
def opencode_target(manager: IntegrationManager) -> Target:
    return manager.targets.get("test-opencode")  # type: ignore[return-value]


# ── Block engine (pure functions) ─────────────────────────────────────────────


class TestAgentsMdBlock:
    def test_render_block_format(self) -> None:
        block = render_agents_md_block(BLOCK_BODY, VERSION)
        lines = block.splitlines()
        assert lines[0] == f"<!-- mnemos:integration:v{VERSION} BEGIN -->"
        assert lines[-1] == f"<!-- mnemos:integration:v{VERSION} END -->"
        assert "# Mnemos memory — always-on rules" in block

    def test_render_block_appends_missing_newline(self) -> None:
        block = render_agents_md_block("no trailing newline", VERSION)
        assert block.endswith(f"<!-- mnemos:integration:v{VERSION} END -->\n")

    def test_strip_removes_block_preserves_user_content(self) -> None:
        content = USER_HEADER + render_agents_md_block(BLOCK_BODY, VERSION) + USER_FOOTER
        cleaned, version = strip_agents_md_block(content)
        assert version == VERSION
        assert cleaned == USER_HEADER + USER_FOOTER

    def test_strip_no_block_returns_content_unchanged(self) -> None:
        cleaned, version = strip_agents_md_block(USER_HEADER)
        assert cleaned == USER_HEADER
        assert version is None

    def test_strip_unpaired_marker_left_alone(self) -> None:
        """A BEGIN without its END must never be removed — could eat user text."""
        content = USER_HEADER + f"<!-- mnemos:integration:v{VERSION} BEGIN -->\n"
        cleaned, version = strip_agents_md_block(content)
        assert cleaned == content
        assert version is None

    def test_strip_handles_mismatched_marker_versions(self) -> None:
        """A crashed update may leave BEGIN/END versions unpaired — still one block."""
        content = (
            USER_HEADER
            + f"<!-- mnemos:integration:v1.0.0 BEGIN -->\n{BLOCK_BODY}"
            + "<!-- mnemos:integration:v1.0.1 END -->\n"
            + USER_FOOTER
        )
        cleaned, version = strip_agents_md_block(content)
        assert version == "1.0.0"
        assert cleaned == USER_HEADER + USER_FOOTER

    def test_strip_removes_multiple_blocks(self) -> None:
        content = (
            render_agents_md_block("old pack\n", OLD_VERSION)
            + USER_HEADER
            + render_agents_md_block(BLOCK_BODY, VERSION)
        )
        cleaned, version = strip_agents_md_block(content)
        assert version == OLD_VERSION
        assert cleaned == USER_HEADER

    def test_read_version_present_and_absent(self) -> None:
        assert read_agents_md_version(render_agents_md_block(BLOCK_BODY, VERSION)) == VERSION
        assert read_agents_md_version(USER_HEADER) is None


# ── Deploy ────────────────────────────────────────────────────────────────────


class TestAgentsMdDeploy:
    def test_deploy_injects_block_into_existing_user_file(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        dest = agents_target.deploy_map["agents_md"]
        dest.parent.mkdir(parents=True)
        dest.write_text(USER_HEADER, encoding="utf-8")

        result = manager.deploy("test-agents")
        file_result = next(f for f in result.files if f.destination == dest)
        assert file_result.status == DeployStatus.DEPLOYED

        content = dest.read_text(encoding="utf-8")
        assert content.startswith(USER_HEADER)
        assert read_agents_md_version(content) == VERSION

    def test_deploy_creates_file_when_missing(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        dest = agents_target.deploy_map["agents_md"]
        manager.deploy("test-agents")
        content = dest.read_text(encoding="utf-8")
        assert read_agents_md_version(content) == VERSION
        assert BLOCK_BODY.strip() in content

    def test_deploy_idempotent_second_run_current(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        manager.deploy("test-agents")
        before = agents_target.deploy_map["agents_md"].read_text(encoding="utf-8")

        result = manager.deploy("test-agents")
        file_result = next(
            f for f in result.files if f.destination == agents_target.deploy_map["agents_md"]
        )
        assert file_result.status == DeployStatus.CURRENT
        assert agents_target.deploy_map["agents_md"].read_text(encoding="utf-8") == before

    def test_deploy_repairs_missing_trailing_newline(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        """User's last line must never be glued to the BEGIN marker."""
        dest = agents_target.deploy_map["agents_md"]
        dest.parent.mkdir(parents=True)
        dest.write_text(USER_HEADER.rstrip("\n"), encoding="utf-8")

        manager.deploy("test-agents")
        content = dest.read_text(encoding="utf-8")
        assert "Always answer in English.<!--" not in content
        assert content.startswith(USER_HEADER)

    def test_deploy_dry_run_does_not_write(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        dest = agents_target.deploy_map["agents_md"]
        dest.parent.mkdir(parents=True)
        dest.write_text(USER_HEADER, encoding="utf-8")

        manager.deploy("test-agents", dry_run=True)
        assert dest.read_text(encoding="utf-8") == USER_HEADER

    def test_deploy_replaces_drifted_block_same_version(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        """Content drift inside the block (same version) is restored on deploy."""
        dest = agents_target.deploy_map["agents_md"]
        manager.deploy("test-agents")
        drifted = dest.read_text(encoding="utf-8").replace(
            "Recall at session start.", "USER EDITED THE BLOCK"
        )
        dest.write_text(drifted, encoding="utf-8")

        result = manager.deploy("test-agents")
        file_result = next(f for f in result.files if f.destination == dest)
        assert file_result.status == DeployStatus.UPDATED
        assert "Recall at session start." in dest.read_text(encoding="utf-8")
        assert "USER EDITED THE BLOCK" not in dest.read_text(encoding="utf-8")


# ── Verify / update / uninstall lifecycle ─────────────────────────────────────


class TestAgentsMdLifecycle:
    def test_verify_current_after_deploy(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        manager.deploy("test-agents")
        result = manager.verify("test-agents")
        assert result.all_current

    def test_verify_missing_before_deploy(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        result = manager.verify("test-agents")
        block = next(
            f for f in result.files if f.destination == agents_target.deploy_map["agents_md"]
        )
        assert block.status == DeployStatus.MISSING

    def test_verify_missing_when_file_has_no_block(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        dest = agents_target.deploy_map["agents_md"]
        dest.parent.mkdir(parents=True)
        dest.write_text(USER_HEADER, encoding="utf-8")

        result = manager.verify("test-agents")
        block = next(f for f in result.files if f.destination == dest)
        assert block.status == DeployStatus.MISSING

    def test_verify_stale_on_version_mismatch(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        old_mgr = IntegrationManager(
            version=OLD_VERSION,
            pack_root=manager.pack_root,
            targets_config=manager.targets,
            home=manager.home,
        )
        old_mgr.deploy("test-agents")

        result = manager.verify("test-agents")
        block = next(
            f for f in result.files if f.destination == agents_target.deploy_map["agents_md"]
        )
        assert block.status == DeployStatus.STALE
        assert block.deployed_version == OLD_VERSION

    def test_update_replaces_old_block_preserving_user_content(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        dest = agents_target.deploy_map["agents_md"]
        dest.parent.mkdir(parents=True)
        dest.write_text(USER_HEADER, encoding="utf-8")

        old_mgr = IntegrationManager(
            version=OLD_VERSION,
            pack_root=manager.pack_root,
            targets_config=manager.targets,
            home=manager.home,
        )
        old_mgr.deploy("test-agents")
        # User appends content between the two releases.
        with dest.open("a", encoding="utf-8") as fh:
            fh.write(USER_FOOTER)

        result = manager.update("test-agents")
        block = next(f for f in result.files if f.destination == dest)
        assert block.status == DeployStatus.UPDATED

        content = dest.read_text(encoding="utf-8")
        assert content.startswith(USER_HEADER)
        assert USER_FOOTER.strip() in content
        assert read_agents_md_version(content) == VERSION

    def test_uninstall_removes_block_keeps_user_content(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        dest = agents_target.deploy_map["agents_md"]
        dest.parent.mkdir(parents=True)
        dest.write_text(USER_HEADER, encoding="utf-8")
        manager.deploy("test-agents")

        result = manager.uninstall("test-agents")
        assert dest in result.removed
        assert dest.read_text(encoding="utf-8") == USER_HEADER

    def test_uninstall_removes_file_when_only_block(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        """Deploy created the file — uninstall removes it entirely."""
        dest = agents_target.deploy_map["agents_md"]
        manager.deploy("test-agents")

        result = manager.uninstall("test-agents")
        assert dest in result.removed
        assert not dest.exists()

    def test_uninstall_noop_when_no_block(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        dest = agents_target.deploy_map["agents_md"]
        dest.parent.mkdir(parents=True)
        dest.write_text(USER_HEADER, encoding="utf-8")

        result = manager.uninstall("test-agents")
        assert dest not in result.removed
        assert dest.read_text(encoding="utf-8") == USER_HEADER

    def test_uninstall_dry_run_keeps_block(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        dest = agents_target.deploy_map["agents_md"]
        manager.deploy("test-agents")
        before = dest.read_text(encoding="utf-8")

        result = manager.uninstall("test-agents", dry_run=True)
        assert dest in result.removed  # previewed…
        assert dest.read_text(encoding="utf-8") == before  # …but not executed

    def test_uninstall_on_missing_file_is_noop(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        result = manager.uninstall("test-agents")
        assert agents_target.deploy_map["agents_md"] not in result.removed


class TestAgentsMdNeverClobber:
    def test_full_lifecycle_user_content_byte_identical(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        """Deploy → version-bump update → uninstall: user parts survive intact."""
        dest = agents_target.deploy_map["agents_md"]
        dest.parent.mkdir(parents=True)
        dest.write_text(USER_HEADER, encoding="utf-8")

        manager.deploy("test-agents")
        # User appends a note AFTER the deploy — outside the block.
        with dest.open("a", encoding="utf-8") as fh:
            fh.write(USER_FOOTER)

        old_mgr = IntegrationManager(
            version=OLD_VERSION,
            pack_root=manager.pack_root,
            targets_config=manager.targets,
            home=manager.home,
        )
        old_mgr.deploy("test-agents")
        manager.update("test-agents")  # OLD_VERSION block → VERSION block

        manager.uninstall("test-agents")
        assert dest.read_text(encoding="utf-8") == USER_HEADER + USER_FOOTER

    def test_update_does_not_duplicate_block(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        dest = agents_target.deploy_map["agents_md"]
        for _ in range(3):
            manager.deploy("test-agents")
        content = dest.read_text(encoding="utf-8")
        assert content.count("BEGIN -->") == 1
        assert content.count("END -->") == 1

    def test_verify_detects_user_edits_inside_block(
        self, manager: IntegrationManager, agents_target: Target
    ) -> None:
        dest = agents_target.deploy_map["agents_md"]
        manager.deploy("test-agents")
        content = dest.read_text(encoding="utf-8")
        dest.write_text(content.replace("Recall at session start.", "TAMPERED"), encoding="utf-8")

        result = manager.verify("test-agents")
        block = next(
            f for f in result.files if f.destination == agents_target.deploy_map["agents_md"]
        )
        assert block.status == DeployStatus.STALE


# ── opencode target ───────────────────────────────────────────────────────────


class TestOpenCodeTarget:
    def test_deploy_skills_nested_and_agents_md(
        self, manager: IntegrationManager, opencode_target: Target
    ) -> None:
        result = manager.deploy("test-opencode")

        skill = opencode_target.deploy_map["skills"] / "mnemos-recall" / "SKILL.md"
        assert skill.exists()
        assert read_stamp(skill.read_text(encoding="utf-8")) == VERSION

        agents_md = opencode_target.deploy_map["agents_md"]
        assert read_agents_md_version(agents_md.read_text(encoding="utf-8")) == VERSION

        block = next(
            f for f in result.files if f.destination == opencode_target.deploy_map["agents_md"]
        )
        assert block.status == DeployStatus.DEPLOYED

    def test_register_mcp_opencode_creates_config(
        self, manager: IntegrationManager, opencode_target: Target
    ) -> None:
        ok, _ = manager.register_mcp("test-opencode")
        assert ok

        cfg_path = Path(str(opencode_target.mcp_config))
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        entry = data["mcp"]["mnemos"]
        assert entry["type"] == "local"
        assert entry["command"][1] == "mcp-server"
        assert entry["enabled"] is True
        assert entry["environment"]["MNEMOS_DATA_DIR"] == str(manager.home / ".mnemos/data")

    def test_register_mcp_opencode_preserves_existing_content(
        self, manager: IntegrationManager, opencode_target: Target
    ) -> None:
        cfg_path = Path(str(opencode_target.mcp_config))
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "mcp": {
                        "other-server": {"type": "local", "command": ["foo", "bar"]},
                    },
                }
            ),
            encoding="utf-8",
        )

        ok, _ = manager.register_mcp("test-opencode")
        assert ok

        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert data["theme"] == "dark"
        assert data["mcp"]["other-server"] == {"type": "local", "command": ["foo", "bar"]}
        assert "mnemos" in data["mcp"]

    def test_register_mcp_opencode_preserves_user_environment(
        self, manager: IntegrationManager, opencode_target: Target
    ) -> None:
        cfg_path = Path(str(opencode_target.mcp_config))
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        tuned = {"MNEMOS_DATA_DIR": "/custom/data"}
        cfg_path.write_text(
            json.dumps(
                {"mcp": {"mnemos": {"type": "local", "command": ["old"], "environment": tuned}}}
            ),
            encoding="utf-8",
        )

        ok, _ = manager.register_mcp("test-opencode")
        assert ok

        entry = json.loads(cfg_path.read_text(encoding="utf-8"))["mcp"]["mnemos"]
        assert entry["environment"]["MNEMOS_DATA_DIR"] == "/custom/data"
        assert entry["environment"]["MNEMOS_VAULT__VAULT_PATH"] == str(
            manager.home / ".mnemos/vault"
        )
        assert entry["command"][0] != "old"  # command refreshed from pack defaults

    def test_register_mcp_opencode_idempotent(
        self, manager: IntegrationManager, opencode_target: Target
    ) -> None:
        manager.register_mcp("test-opencode")
        first = Path(str(opencode_target.mcp_config)).read_text(encoding="utf-8")
        manager.register_mcp("test-opencode")
        assert Path(str(opencode_target.mcp_config)).read_text(encoding="utf-8") == first

    def test_opencode_full_lifecycle(
        self, manager: IntegrationManager, opencode_target: Target
    ) -> None:
        manager.setup("test-opencode", register_mcp=True)

        verify = manager.verify("test-opencode")
        assert verify.all_current

        data = json.loads(Path(str(opencode_target.mcp_config)).read_text(encoding="utf-8"))
        assert "mnemos" in data["mcp"]

        uninstall = manager.uninstall("test-opencode")
        assert opencode_target.deploy_map["agents_md"] in uninstall.removed
        assert not opencode_target.deploy_map["agents_md"].exists()
        # Skills (stamped files) are gone too; the skills dir may remain.
        skill = opencode_target.deploy_map["skills"] / "mnemos-recall" / "SKILL.md"
        assert not skill.exists()


# ── Real shipped targets.yaml ─────────────────────────────────────────────────


class TestRealTargetsSchema:
    """Schema guards over the REAL ``integrations/targets.yaml``."""

    @pytest.fixture
    def real_config(self) -> TargetsConfig:
        return load_targets()

    def test_agents_target_has_agents_md(self, real_config: TargetsConfig) -> None:
        target = real_config.get("agents")
        assert target is not None
        assert str(target.deploy_map["agents_md"]).endswith(".agents/AGENTS.md")

    def test_zcode_target_has_agents_md(self, real_config: TargetsConfig) -> None:
        target = real_config.get("zcode")
        assert target is not None
        assert str(target.deploy_map["agents_md"]).endswith(".zcode/AGENTS.md")

    def test_opencode_target_schema(self, real_config: TargetsConfig) -> None:
        target = real_config.get("opencode")
        assert target is not None
        assert target.layout == "nested"
        assert target.mcp_format == "opencode"
        assert target.mcp_config is not None
        assert str(target.mcp_config).endswith("opencode.json")
        assert str(target.deploy_map["agents_md"]).endswith(".config/opencode/AGENTS.md")

    def test_copilot_family_untouched(self, real_config: TargetsConfig) -> None:
        """Backward compatibility: Copilot-family targets gained no agents_md key."""
        for name in ("copilot", "generic-copilot", "cursor", "hermes", "pi"):
            target = real_config.get(name)
            assert target is not None, name
            assert "agents_md" not in target.deploy_map, name
