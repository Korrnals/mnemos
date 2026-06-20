"""``mnemos doctor`` CLI subcommand — health check.

Runs a series of checks against the local Mnemos installation and reports
status. Exit codes:

* 0 — all checks pass
* 1 — one or more checks failed
* 2 — one or more checks warn (e.g. stale integration) but nothing is broken
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from mnemos import __version__
from mnemos.config import load_settings

console = Console()

doctor_app = typer.Typer(
    name="doctor",
    help="Run Mnemos health checks (config, vault, DB, MCP, integration, tags).",
    no_args_is_help=False,
)


# ── Check result model ────────────────────────────────────────────────────────


class CheckStatus(StrEnum):
    PASS = "pass"
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
        except Exception:  # one target failing shouldn't abort
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
    for check in (_check_mcp_server, _check_integration):
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
) -> None:
    """Run Mnemos health checks and report status.

    Checks: config, data dir, vault, SQLite DB, vector store, MCP server,
    integration layer, tag contract.

    Exit codes: 0 = all pass, 1 = one or more failed, 2 = warnings only.
    """
    results = _run_all_checks()

    if json_output:
        exit_code = _exit_code(results)
        payload = {
            "version": __version__,
            "checks": [
                {"name": r.name, "status": r.status.value, "detail": r.detail} for r in results
            ],
            "exit_code": exit_code,
        }
        console.print_json(json.dumps(payload))
        raise typer.Exit(exit_code)

    _render(results)
    code = _exit_code(results)
    console.print()
    if code == 0:
        console.print("[green]All checks passed. Mnemos is healthy.[/green]")
    elif code == 2:
        console.print("[yellow]⚠ Some checks warn — see above.[/yellow]")
    else:
        console.print("[red]✗ One or more checks failed — see above.[/red]")
    raise typer.Exit(code)


if __name__ == "__main__":  # pragma: no cover — manual invocation
    doctor()
