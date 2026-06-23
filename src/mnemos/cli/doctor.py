"""``mnemos doctor`` CLI subcommand — health check.

Runs a series of checks against the local Mnemos installation and reports
status. Exit codes:

* 0 — all checks pass
* 1 — one or more checks failed
* 2 — one or more checks warn (e.g. stale integration) but nothing is broken
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from mnemos import __version__
from mnemos.config import load_settings

logger = logging.getLogger(__name__)
console = Console()

doctor_app = typer.Typer(
    name="doctor",
    help="Run Mnemos health checks (config, vault, DB, MCP, integration, tags).",
    no_args_is_help=False,
)


# ── Check result model ────────────────────────────────────────────────────────


class CheckStatus(StrEnum):
    PASS = "pass"  # nosec B105 — status enum value, not a password
    WARN = "warn"
    FAIL = "fail"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# ── Individual checks ─────────────────────────────────────────────────────────


def _check_config() -> CheckResult:
    """Config loads and is valid."""
    try:
        settings = load_settings()
        settings.resolve_paths()
    except Exception as exc:  # doctor must report, not crash
        return CheckResult("Config", CheckStatus.FAIL, f"load failed: {exc}")
    cfg_path = os.environ.get("MNEMOS_CONFIG") or str(Path.home() / ".mnemos" / "config.yaml")
    return CheckResult(
        "Config",
        CheckStatus.PASS,
        f"{cfg_path} (valid)",
        extra={"settings": settings},
    )


def _check_data_dir(settings: Any) -> CheckResult:
    """Data dir exists and is writable."""
    data_dir = settings.mnemos.data_dir
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        test_file = data_dir / ".mnemos_doctor_write_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
    except OSError as exc:
        return CheckResult("Data dir", CheckStatus.FAIL, f"{data_dir} not writable: {exc}")

    # Size estimate.
    try:
        total = sum(f.stat().st_size for f in data_dir.rglob("*") if f.is_file())
        size_mb = total / (1024 * 1024)
        size_str = f"{size_mb:.1f} MB"
    except OSError:
        size_str = "size unknown"

    return CheckResult("Data dir", CheckStatus.PASS, f"{data_dir} (writable, {size_str})")


def _check_vault(settings: Any) -> CheckResult:
    """Vault path exists, is writable, and count markdown files."""
    vault = settings.mnemos.vault_path
    try:
        vault.mkdir(parents=True, exist_ok=True)
        test_file = vault / ".mnemos_doctor_write_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
    except OSError as exc:
        return CheckResult("Vault", CheckStatus.FAIL, f"{vault} not writable: {exc}")

    note_count = sum(1 for _ in vault.rglob("*.md"))
    return CheckResult("Vault", CheckStatus.PASS, f"{vault} (writable, {note_count} notes)")


def _check_sqlite(settings: Any) -> CheckResult:
    """SQLite DB exists, opens, and reports row count."""
    db_path = settings.db_path
    if not db_path.exists():
        return CheckResult(
            "SQLite DB",
            CheckStatus.WARN,
            f"{db_path} (missing — run `mnemos add` to create)",
        )
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
            count = row[0] if row else 0
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return CheckResult("SQLite DB", CheckStatus.FAIL, f"{db_path} (error: {exc})")
    return CheckResult("SQLite DB", CheckStatus.PASS, f"{db_path} (healthy, {count:,} entries)")


def _check_vector_store(settings: Any) -> CheckResult:
    """Vector store DB exists, opens, and reports embedding count."""
    vectors_path = settings.mnemos.data_dir / "vectors.db"
    if not vectors_path.exists():
        return CheckResult(
            "Vector store",
            CheckStatus.WARN,
            f"{vectors_path} (missing — created on first add)",
        )
    try:
        conn = sqlite3.connect(str(vectors_path))
        try:
            # The table name is `embeddings` in the current VectorStore schema.
            row = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()
            count = row[0] if row else 0
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return CheckResult("Vector store", CheckStatus.FAIL, f"{vectors_path} (error: {exc})")
    return CheckResult(
        "Vector store",
        CheckStatus.PASS,
        f"{vectors_path} (healthy, {count:,} embeddings)",
    )


def _check_mcp_server() -> CheckResult:
    """Check VS Code mcp.json for a `mnemos` entry (user or workspace scope)."""
    candidates = [
        Path.home() / ".config" / "Code" / "User" / "mcp.json",
        Path.cwd() / ".vscode" / "mcp.json",
    ]
    for cfg_path in candidates:
        if not cfg_path.exists():
            continue
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        servers = data.get("servers", {}) if isinstance(data, dict) else {}
        if "mnemos" in servers:
            scope = "user" if cfg_path == candidates[0] else "workspace"
            return CheckResult(
                "MCP server",
                CheckStatus.PASS,
                f"registered in VS Code ({scope} scope) — {cfg_path}",
            )
    return CheckResult(
        "MCP server",
        CheckStatus.WARN,
        "not registered in VS Code — run `mnemos integration setup` or mcp-setup.sh",
    )


def _check_integration() -> CheckResult:
    """Verify integration layer: installed version + stale status."""
    try:
        from mnemos.cli.integration import IntegrationManager, load_targets

        mgr = IntegrationManager(version=__version__)
        cfg = load_targets()
        detected = cfg.detected()
    except Exception as exc:  # doctor reports, doesn't crash
        return CheckResult("Integration", CheckStatus.FAIL, f"init failed: {exc}")

    if not detected:
        return CheckResult(
            "Integration",
            CheckStatus.WARN,
            "no agent harnesses detected — run `mnemos integration detect`",
        )

    # Aggregate verify across all detected targets.
    total_stale = 0
    total_missing = 0
    target_names: list[str] = []
    for target in detected:
        target_names.append(target.name)
        try:
            result = mgr.verify(target.name)
            total_stale += result.stale_count
            total_missing += result.missing_count
        except Exception as exc:  # one target failing shouldn't abort
            logger.warning("integration verify failed for target %s: %s", target.name, exc)
            total_missing += 1

    if total_missing > 0:
        return CheckResult(
            "Integration",
            CheckStatus.WARN,
            f"installed v{__version__}, targets: {', '.join(target_names)}, "
            f"{total_missing} missing file(s) — run `mnemos integration setup`",
        )
    if total_stale > 0:
        return CheckResult(
            "Integration",
            CheckStatus.WARN,
            f"installed v{__version__}, targets: {', '.join(target_names)}, "
            f"{total_stale} stale — run `mnemos integration update`",
        )
    return CheckResult(
        "Integration",
        CheckStatus.PASS,
        f"installed v{__version__}, targets: {', '.join(target_names)}, stale: no",
    )


def _check_tag_contract(settings: Any) -> CheckResult:
    """Report tag contract mode + non-conformant entry count (if fast)."""
    strict = settings.mnemos.strict_tag_contract
    mode = "strict" if strict else "lenient"

    # Count non-conformant entries only if the DB exists and the scan is cheap.
    db_path = settings.db_path
    if not db_path.exists():
        return CheckResult(
            "Tag contract",
            CheckStatus.WARN,
            f"{mode} mode, DB missing — cannot scan",
        )

    non_conformant = 0
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            # tags is stored as JSON array; we check for project:/agent:/gcw: prefixes.
            rows = conn.execute("SELECT tags FROM memories").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return CheckResult("Tag contract", CheckStatus.FAIL, f"{mode} mode, scan error: {exc}")

    for (raw_tags,) in rows:
        try:
            tags = json.loads(raw_tags) if raw_tags else []
        except (json.JSONDecodeError, TypeError):
            non_conformant += 1
            continue
        has_project = any(t.startswith("project:") for t in tags)
        has_agent = any(t.startswith("agent:") for t in tags)
        has_gcw = any(t.startswith("gcw:") for t in tags)
        if not (has_project and has_agent and has_gcw):
            non_conformant += 1

    if non_conformant > 0:
        return CheckResult(
            "Tag contract",
            CheckStatus.WARN,
            f"{mode} mode, {non_conformant} non-conformant entries",
        )
    return CheckResult(
        "Tag contract", CheckStatus.PASS, f"{mode} mode, {non_conformant} non-conformant entries"
    )


def _check_agent_wiring() -> CheckResult:
    """Check GCW agent MCP wiring status in ``~/.copilot/agents``.

    * PASS — all detected agents have mnemos tools wired (or are skipped
      via ``tool_profile``).
    * WARN — some agents are unwired (lists the count).
    * SKIP — no agents directory found (non-GCW setup).
    """
    try:
        from mnemos.cli.agent_wiring import DEFAULT_AGENTS_DIR, verify_agents

        if not DEFAULT_AGENTS_DIR.is_dir():
            return CheckResult(
                "Agent wiring",
                CheckStatus.WARN,
                f"no agents directory at {DEFAULT_AGENTS_DIR} (non-GCW setup)",
            )

        summary = verify_agents()
    except Exception as exc:  # doctor reports, doesn't crash
        return CheckResult("Agent wiring", CheckStatus.FAIL, f"check crashed: {exc}")

    if summary.total == 0:
        return CheckResult(
            "Agent wiring",
            CheckStatus.WARN,
            f"no .agent.md files in {DEFAULT_AGENTS_DIR}",
        )

    if summary.unwired == 0 and summary.errors == 0:
        return CheckResult(
            "Agent wiring",
            CheckStatus.PASS,
            f"{summary.wired}/{summary.total} wired, "
            f"{summary.skipped_tool_profile} skipped (tool_profile)",
        )

    return CheckResult(
        "Agent wiring",
        CheckStatus.WARN,
        f"{summary.wired}/{summary.total} wired, {summary.unwired} unwired, "
        f"{summary.skipped_tool_profile} skipped — run "
        "`mnemos integration setup --wire-agents --all`",
    )


# ── Paths overview ────────────────────────────────────────────────────────────


def _collect_paths(settings: Any) -> dict[str, str]:
    """Collect all relevant Mnemos paths as display strings.

    Returns a dict with keys: root, config, data_dir, db_path, vault, logs,
    cache, completion, mcp_config.
    """
    home = Path.home()
    root = home / ".mnemos"
    mcp_cfg = home / ".config" / "Code" / "User" / "mcp.json"

    # Use ~ abbreviation for display where possible.
    def _display(p: Path) -> str:
        s = str(p)
        home_s = str(home)
        if s.startswith(home_s):
            return "~" + s[len(home_s):]
        return s

    return {
        "root": _display(root),
        "config": _display(root / "config.yaml"),
        "data_dir": _display(settings.mnemos.data_dir),
        "db_path": _display(settings.db_path),
        "vault": _display(settings.mnemos.vault_path),
        "logs": _display(settings.logging.log_file)
        if settings.logging.log_file
        else "(stderr only)",
        "cache": _display(root / "cache"),
        "completion": _display(root / "completion"),
        "mcp_config": _display(mcp_cfg),
    }


def _render_paths(paths: dict[str, str]) -> None:
    """Print the paths table to the console."""
    console.print()
    console.print("[bold cyan]── Paths ──────────────────────────────────────[/bold cyan]")
    labels = [
        ("Root", "root"),
        ("Config", "config"),
        ("Data dir", "data_dir"),
        ("DB", "db_path"),
        ("Vault", "vault"),
        ("Logs", "logs"),
        ("Cache", "cache"),
        ("Completion", "completion"),
        ("MCP config", "mcp_config"),
    ]
    for label, key in labels:
        console.print(f"  {label:<13} {paths.get(key, '?')}")


# ── Runner ───────────────────────────────────────────────────────────────────


# Settings-dependent checks (take the resolved settings object).
_SETTINGS_CHECKS = (
    _check_data_dir,
    _check_vault,
    _check_sqlite,
    _check_vector_store,
    _check_tag_contract,
)


def _run_all_checks() -> list[CheckResult]:
    """Run every check in order, threading the settings object through.

    The doctor must never crash the CLI: each check is wrapped so an
    unexpected exception becomes a FAIL result with the exception message.
    """
    results: list[CheckResult] = []

    # Config first — other checks depend on its resolved settings.
    try:
        result = _check_config()
        settings: Any = result.extra.get("settings")
        results.append(result)
    except Exception as exc:  # doctor must never crash
        results.append(CheckResult("_check_config", CheckStatus.FAIL, f"check crashed: {exc}"))
        settings = None

    # No-arg checks.
    for check in (_check_mcp_server, _check_integration, _check_agent_wiring):
        try:
            results.append(check())
        except Exception as exc:  # doctor must never crash
            results.append(CheckResult(check.__name__, CheckStatus.FAIL, f"check crashed: {exc}"))

    # Settings-dependent checks.
    for settings_check in _SETTINGS_CHECKS:
        try:
            results.append(settings_check(settings))
        except Exception as exc:  # doctor must never crash
            name = settings_check.__name__
            results.append(CheckResult(name, CheckStatus.FAIL, f"check crashed: {exc}"))

    return results


def _exit_code(results: list[CheckResult]) -> int:
    """Compute the exit code from the check results."""
    if any(r.status == CheckStatus.FAIL for r in results):
        return 1
    if any(r.status == CheckStatus.WARN for r in results):
        return 2
    return 0


def _warn_names(results: list[CheckResult]) -> list[str]:
    """Names of all WARN-level checks in the results."""
    return [r.name for r in results if r.status == CheckStatus.WARN]


# ── Auto-fix actions (--fix) ──────────────────────────────────────────────────


@dataclass
class _FixAction:
    """A callable that attempts to fix a WARN-level check."""

    description: str
    run: Callable[[], tuple[bool, str]]


def _fix_integration_stale() -> tuple[bool, str]:
    """Run ``integration update`` to bring stale files to current version."""
    from mnemos.cli.integration import IntegrationManager, load_targets

    mgr = IntegrationManager(version=__version__)
    cfg = load_targets()
    detected = cfg.detected()
    if not detected:
        return False, "no agent harnesses detected"
    updated = 0
    for target in detected:
        mgr.update(target.name)
        # Verify after update — count targets that are now fully current.
        verify = mgr.verify(target.name)
        if verify.stale_count == 0 and verify.missing_count == 0:
            updated += 1
    return True, f"updated {updated}/{len(detected)} target(s) to v{__version__}"


def _fix_agent_wiring() -> tuple[bool, str]:
    """Wire mnemos/* into all unwired GCW agents."""
    from mnemos.cli.agent_wiring import detect_agents, wire_agents
    from mnemos.cli.util import _resolve_agents_to_wire

    agents = detect_agents()
    if not agents:
        return False, "no agents found"
    to_wire = _resolve_agents_to_wire(agents, select=None, wire_all=True)
    if not to_wire:
        return True, "all agents already wired"
    results = wire_agents(to_wire, mode="wildcard")
    wired = sum(1 for r in results if r.status.value == "wired")
    return True, f"wired {wired}/{len(to_wire)} agent(s)"


def _fix_mcp_registration() -> tuple[bool, str]:
    """Register the MCP server via mcp-setup.sh."""
    from mnemos.cli.integration import IntegrationManager

    mgr = IntegrationManager(version=__version__)
    ok, note = mgr.register_mcp()
    return ok, note


def _fix_action_for(check_name: str) -> _FixAction | None:
    """Return the fix action for a WARN-level check, or None if not fixable."""
    actions: dict[str, _FixAction] = {
        "Integration": _FixAction(
            description="mnemos integration update (redeploy stale files)",
            run=_fix_integration_stale,
        ),
        "Agent wiring": _FixAction(
            description="mnemos integration setup --wire-agents --all",
            run=_fix_agent_wiring,
        ),
        "MCP server": _FixAction(
            description="MCP server registration (mcp-setup.sh)",
            run=_fix_mcp_registration,
        ),
    }
    return actions.get(check_name)


def _render(results: list[CheckResult]) -> None:
    """Render the results as a rich table."""
    table = Table(title="Mnemos Health Check", show_header=True, header_style="bold")
    table.add_column("Status", style="bold", width=4)
    table.add_column("Check", style="bold cyan")
    table.add_column("Detail")

    for r in results:
        if r.status == CheckStatus.PASS:
            icon = "[green]✓[/green]"
        elif r.status == CheckStatus.WARN:
            icon = "[yellow]⚠[/yellow]"
        else:
            icon = "[red]✗[/red]"
        table.add_row(icon, r.name, r.detail)

    console.print(table)


# ── Command ───────────────────────────────────────────────────────────────────


@doctor_app.callback(invoke_without_command=True)
def doctor(
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit results as JSON (for scripting / CI) instead of a table.",
        ),
    ] = False,
    fix: Annotated[
        bool,
        typer.Option(
            "--fix",
            help="Auto-fix WARN-level checks (stale integration, unwired agents, "
            "missing MCP registration). FAIL-level checks are not auto-fixable.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="With --fix: preview what would be fixed without executing.",
        ),
    ] = False,
    paths_only: Annotated[
        bool,
        typer.Option(
            "--paths",
            help="Show only the paths overview table (skip health checks). "
            "Useful for quick reference.",
        ),
    ] = False,
) -> None:
    """Run Mnemos health checks and report status.

    Checks: config, data dir, vault, SQLite DB, vector store, MCP server,
    integration layer, agent wiring, tag contract.

    Exit codes: 0 = all pass, 1 = one or more failed, 2 = warnings only.

    With ``--fix``: attempts to auto-fix WARN-level checks (stale
    integration → ``integration update``, unwired agents →
    ``integration setup --wire-agents --all``, missing MCP → MCP setup).
    After fixes, re-runs the affected checks and reports the new status.
    ``--fix --dry-run`` previews what would be fixed without executing.

    With ``--paths``: prints only the paths overview table and exits 0.
    """
    # ── --paths: quick reference, no health checks ──────────────────────
    if paths_only:
        try:
            settings = load_settings()
            settings.resolve_paths()
        except Exception as exc:  # doctor must not crash
            console.print(f"[red]✗[/red] Cannot load settings: {exc}")
            raise typer.Exit(1) from exc
        paths = _collect_paths(settings)
        if json_output:
            console.print_json(json.dumps({"paths": paths}))
        else:
            _render_paths(paths)
        raise typer.Exit(0)

    results = _run_all_checks()

    # Collect paths from the config check's settings (already loaded).
    settings_obj: Any = None
    for r in results:
        if r.name == "Config":
            settings_obj = r.extra.get("settings")
            break
    paths = _collect_paths(settings_obj) if settings_obj else {}

    fixed: list[str] = []
    fix_skipped: list[str] = []

    if fix:
        if dry_run:
            console.print("\n[cyan][dry-run][/cyan] Preview of auto-fixes:")
        for r in results:
            if r.status != CheckStatus.WARN:
                continue
            action = _fix_action_for(r.name)
            if action is None:
                fix_skipped.append(r.name)
                continue
            if dry_run:
                console.print(f"  [yellow]⚠[/yellow] {r.name} → would run: {action.description}")
                continue
            console.print(f"\n[yellow]⚠[/yellow] {r.name} → fixing...")
            ok, note = action.run()
            if ok:
                console.print(f"  [green]✓[/green] {r.name}: {note}")
                fixed.append(r.name)
            else:
                console.print(f"  [red]✗[/red] {r.name}: {note}")
                fix_skipped.append(r.name)

        if not dry_run and fixed:
            # Re-run the fixed checks to confirm the new status.
            new_results = _run_all_checks()
            results = new_results

    if json_output:
        exit_code = _exit_code(results)
        payload: dict[str, Any] = {
            "version": __version__,
            "checks": [
                {"name": r.name, "status": r.status.value, "detail": r.detail} for r in results
            ],
            "paths": paths,
            "exit_code": exit_code,
        }
        if fix:
            payload["fixed"] = fixed
            payload["fix_skipped"] = fix_skipped
            payload["dry_run"] = dry_run
        console.print_json(json.dumps(payload))
        raise typer.Exit(exit_code)

    _render(results)
    if paths:
        _render_paths(paths)
    code = _exit_code(results)
    console.print()
    if fix:
        if dry_run:
            console.print(f"[cyan][dry-run][/cyan] Would fix {len(_warn_names(results))} issue(s).")
        elif fixed:
            console.print(f"[green]Fixed: {len(fixed)} issue(s).[/green]")
        if fix_skipped:
            console.print(f"[yellow]Could not auto-fix: {', '.join(fix_skipped)}[/yellow]")
    if code == 0:
        console.print("[green]All checks passed. Mnemos is healthy.[/green]")
    elif code == 2:
        console.print("[yellow]⚠ Some checks warn — see above.[/yellow]")
    else:
        console.print("[red]✗ One or more checks failed — see above.[/red]")
    raise typer.Exit(code)


if __name__ == "__main__":  # pragma: no cover — manual invocation
    doctor()
