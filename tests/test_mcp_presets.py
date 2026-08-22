"""Drift guard for the published MCP presets and adapter template (#124).

The presets (``integrations/mcp-presets.md``) and the template
(``integrations/adapter-template.md``) are copy-paste configuration — a
silent edit to the wire contract breaks every user who pastes them. These
tests parse the markdown artefacts themselves and validate every config
fragment against the canonical ADR-0017 D1 stdio entry:

    command = "mnemos", args = ["mcp-server"], type = "stdio"

Deliberately imports no ``mnemos`` module: the artefacts are plain files,
so the guard runs identically from any interpreter / install layout.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PRESETS = REPO_ROOT / "integrations" / "mcp-presets.md"
TEMPLATE = REPO_ROOT / "integrations" / "adapter-template.md"

#: The wire contract every preset/fragment must carry (ADR-0017 D1).
CANONICAL_COMMAND = "mnemos"
CANONICAL_ARGS = ["mcp-server"]
CANONICAL_TYPE = "stdio"


# ── helpers ───────────────────────────────────────────────────────────────────


def fenced_blocks(text: str, lang: str) -> list[str]:
    """Return the bodies of all ```lang fenced blocks in *text*."""
    return re.findall(rf"```{lang}\n(.*?)```", text, flags=re.DOTALL)


def assert_json_server_entry(entry: object) -> None:
    """Assert a parsed JSON server entry matches the canonical contract."""
    assert isinstance(entry, dict), f"server entry is not an object: {entry!r}"
    assert entry.get("command") == CANONICAL_COMMAND
    assert entry.get("args") == CANONICAL_ARGS
    assert entry.get("type") == CANONICAL_TYPE


def parse_json_fragment(block: str) -> dict[str, object]:
    """Parse a `"mnemos": {…}` fragment line by wrapping it in an object."""
    payload = json.loads("{" + block.strip() + "}")
    assert isinstance(payload, dict)
    return payload


def dig_server(payload: dict[str, object]) -> object:
    """Extract the mnemos entry from an mcpServers/servers config file."""
    for key in ("mcpServers", "servers"):
        servers = payload.get(key)
        if isinstance(servers, dict) and "mnemos" in servers:
            return servers["mnemos"]
    raise AssertionError(f"no mcpServers.mnemos in payload: {payload!r}")


def assert_toml_server_table(block: str) -> None:
    """Assert a Codex-style TOML block defines the canonical entry."""
    data = tomllib.loads(block)
    entry = data.get("mcp_servers", {}).get("mnemos")
    assert isinstance(entry, dict), f"no [mcp_servers.mnemos] table in: {block!r}"
    assert entry.get("command") == CANONICAL_COMMAND
    assert entry.get("args") == CANONICAL_ARGS


# ── mcp-presets.md ────────────────────────────────────────────────────────────


def test_presets_file_exists() -> None:
    assert PRESETS.is_file(), f"missing published artefact: {PRESETS}"


def test_presets_cover_required_harnesses() -> None:
    text = PRESETS.read_text(encoding="utf-8")
    for harness in ("Cursor", "Claude Code", "Codex", "Windsurf"):
        assert f"## {harness}" in text, f"no preset section for {harness}"


def test_presets_json_blocks_match_wire_contract() -> None:
    """Every JSON block (fragment or full file) carries the canonical entry."""
    text = PRESETS.read_text(encoding="utf-8")
    blocks = fenced_blocks(text, "json")
    # Cursor fragment, Claude full file, Windsurf fragment.
    assert len(blocks) == 3, f"expected 3 JSON blocks in the presets, got {len(blocks)}"
    for block in blocks:
        stripped = block.strip()
        if stripped.startswith('"'):
            entry = parse_json_fragment(stripped)["mnemos"]
        else:
            entry = dig_server(json.loads(stripped))
        assert_json_server_entry(entry)


def test_presets_toml_block_matches_wire_contract() -> None:
    blocks = fenced_blocks(PRESETS.read_text(encoding="utf-8"), "toml")
    assert len(blocks) == 1, "expected exactly one TOML block (Codex preset)"
    assert_toml_server_table(blocks[0])


def test_presets_shell_one_liners_match_wire_contract() -> None:
    """echo/printf one-liners embed the same entry as the paste blocks."""
    for line in PRESETS.read_text(encoding="utf-8").splitlines():
        if "echo '" in line and "mcpServers" in line:
            payload = re.search(r"echo '(\{.*\})'", line)
            assert payload is not None, f"cannot extract JSON payload: {line}"
            assert_json_server_entry(dig_server(json.loads(payload.group(1))))
        if "printf" in line and "mcp_servers.mnemos" in line:
            assert 'command = "mnemos"' in line
            assert 'args = ["mcp-server"]' in line


def test_presets_claude_one_liner_shape() -> None:
    """`claude mcp add` blocks keep the canonical command tail."""
    blocks = fenced_blocks(PRESETS.read_text(encoding="utf-8"), "bash")
    add_blocks = [b for b in blocks if "claude mcp add" in b]
    assert add_blocks, "no `claude mcp add` one-liner found"
    for block in add_blocks:
        assert "-- mnemos mcp-server" in block


def test_presets_no_secrets_in_examples() -> None:
    text = PRESETS.read_text(encoding="utf-8").lower()
    for marker in ("api_key=", "token=", "password=", "sk-"):
        assert marker not in text, f"secret-shaped marker {marker!r} in presets"


# ── adapter-template.md ───────────────────────────────────────────────────────


def test_template_file_exists_and_sized() -> None:
    assert TEMPLATE.is_file(), f"missing published artefact: {TEMPLATE}"
    line_count = len(TEMPLATE.read_text(encoding="utf-8").splitlines())
    # "~100 lines" per issue #124 — a generous band, still one screen.
    assert 80 <= line_count <= 120, f"adapter template drifted to {line_count} lines"


def test_template_has_three_sections_and_checklist() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    for section in ("Connect", "Expose", "Configure", "Acceptance checklist"):
        assert section in text, f"template missing section: {section}"
    items = re.findall(r"^- \[ \] ", text, flags=re.MULTILINE)
    assert len(items) >= 8, f"checklist too thin: {len(items)} items"


def test_template_config_blocks_match_wire_contract() -> None:
    """The template must pass its own checklist item #1: the pasted entry."""
    text = TEMPLATE.read_text(encoding="utf-8")
    json_blocks = fenced_blocks(text, "json")
    toml_blocks = fenced_blocks(text, "toml")
    assert json_blocks and toml_blocks, "template must show JSON and TOML forms"
    for block in json_blocks:
        stripped = block.strip()
        if stripped.startswith('"'):
            entry = parse_json_fragment(stripped)["mnemos"]
        else:
            entry = dig_server(json.loads(stripped))
        assert_json_server_entry(entry)
    for block in toml_blocks:
        assert_toml_server_table(block)


def test_template_exposes_core_tools() -> None:
    """Checklist items 3-5 reference tools the Expose section must list."""
    text = TEMPLATE.read_text(encoding="utf-8")
    for tool in ("mnemos_search", "mnemos_add", "mnemos_agent_recall"):
        assert tool in text, f"Expose section missing core tool: {tool}"


def test_template_states_tag_contract() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    for tag in ("project:", "agent:", "mnemos:"):
        assert tag in text, f"Configure section missing tag kind: {tag}"


# ── README compatibility table (EN + RU) ─────────────────────────────────────


README_TABLE_HEADERS = [
    ("README.md", "One-line MCP preset"),
    ("README.ru.md", "MCP-пресет"),
]


@pytest.mark.parametrize("readme,header", README_TABLE_HEADERS)
def test_readme_compatibility_table_present(readme: str, header: str) -> None:
    text = (REPO_ROOT / readme).read_text(encoding="utf-8")
    assert header in text, f"{readme} missing the compatibility table header"
    for harness in ("Cursor", "Claude Code", "Codex", "Windsurf", "Hermes"):
        assert harness in text, f"{readme} compatibility table missing {harness}"


def test_guides_reference_artefacts() -> None:
    en = (REPO_ROOT / "docs" / "en" / "user" / "integration-guide.md").read_text(
        encoding="utf-8"
    )
    ru = (REPO_ROOT / "docs" / "ru" / "user" / "integration-guide.md").read_text(
        encoding="utf-8"
    )
    for text in (en, ru):
        assert "mcp-presets.md" in text
        assert "adapter-template.md" in text
