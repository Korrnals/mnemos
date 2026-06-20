"""``mnemos util-*`` CLI subcommands — integration layer management.

Subcommand tree::

    mnemos util-detect      — print detected harnesses + deploy paths
    mnemos util-setup       — deploy files + register MCP (unified entry point)
    mnemos util-update      — bring stale files to current version
    mnemos util-verify      — compare deployed files against shipped pack
    mnemos util-uninstall   — remove only stamped files

All commands support ``--dry-run`` and ``--target`` (default: all detected).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mnemos import __version__
from mnemos.cli.integration import (
    DeployResult,
    DeployStatus,
    IntegrationManager,
    VerifyResult,
    load_targets,
)

console = Console()

util_app = typer.Typer(
    name="util",
    help="Manage Mnemos integration layer (instructions, skills, prompts, MCP).",
    no_args_is_help=True,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _manager(pack_root: Path | None = None) -> IntegrationManager:
    """Build an IntegrationManager bound to the current package version."""
    return IntegrationManager(version=__version__, pack_root=pack_root)


def _resolve_targets(target: str) -> list[str]:
    """Resolve ``--target`` value to a concrete list of target names.

    ``all`` → every detected target. A specific name is validated against
    the config and must be detected (or we warn and skip).
    """
    cfg = load_targets()
    if target == "all":
        detected = cfg.detected()
        if not detected:
            console.print("[yellow]No agent harnesses detected.[/yellow]")
            console.print(
                "  Looked for: "
                + ", ".join(str(p) for t in cfg.targets for p in t.detect_paths)
            )
            return []
        return [t.name for t in detected]

    tgt = cfg.get(target)
    if tgt is None:
        console.print(f"[red]Unknown target: {target}[/red]")
        console.print(f"  Available: {', '.join(t.name for t in cfg.targets)}")
        raise typer.Exit(1)

    if not tgt.is_detected():
        console.print(f"[yellow]Target {target!r} not detected (paths missing).[/yellow]")
        console.print("  Detect paths:")
        for p in tgt.detect_paths:
            console.print(f"    {p} {'✓' if p.exists() else '✗'}")
        return []

    return [target]


def _print_deploy_result(result: DeployResult, *, dry_run: bool) -> None:
    """Pretty-print a DeployResult."""
    prefix = "[dry-run] " if dry_run else ""
    table = Table(title=f"{prefix}Target: {result.target_name}", show_lines=False)
    table.add_column("Status", style="bold")
    table.add_column("File")
    table.add_column("Version")
    table.add_column("Note")

    for f in result.files:
        status_style = {
            DeployStatus.DEPLOYED: "green",
            DeployStatus.UPDATED: "yellow",
            DeployStatus.CURRENT: "dim",
            DeployStatus.SKIPPED: "dim",
        }.get(f.status, "white")
        table.add_row(
            f"[{status_style}]{f.status.value}[/{status_style}]",
            str(f.destination),
            f.deployed_version or "—",
            f.note,
        )
    console.print(table)

    if result.mcp_registered or result.mcp_note:
        icon = "[green]✓[/green]" if result.mcp_registered else "[yellow]⚠[/yellow]"
        console.print(f"  {icon} MCP: {result.mcp_note}")


def _print_verify_result(result: VerifyResult) -> None:
    """Pretty-print a VerifyResult."""
    table = Table(title=f"Verify: {result.target_name}")
    table.add_column("Status", style="bold")
    table.add_column("File")
    table.add_column("Deployed")
    table.add_column("Note")

    for f in result.files:
        status_style = {
            DeployStatus.CURRENT: "green",
            DeployStatus.STALE: "yellow",
            DeployStatus.MISSING: "red",
            DeployStatus.SKIPPED: "dim",
        }.get(f.status, "white")
        table.add_row(
            f"[{status_style}]{f.status.value}[/{status_style}]",
            str(f.destination),
            f.deployed_version or "—",
            f.note,
        )
    console.print(table)


# ── Commands ──────────────────────────────────────────────────────────────────


@util_app.command(name="detect")
def detect_cmd() -> None:
    """Print detected agent harnesses and their deploy paths."""
    cfg = load_targets()
    detected = cfg.detected()

    if not detected:
        console.print("[yellow]No agent harnesses detected.[/yellow]")
        console.print("\nSearched for:")
        for t in cfg.targets:
            for p in t.detect_paths:
                console.print(f"  {t.name}: {p}")
        return

    table = Table(title="Detected harnesses")
    table.add_column("Target", style="bold cyan")
    table.add_column("Detect path")
    table.add_column("Deploy map")

    for t in detected:
        detect_str = "\n".join(str(p) for p in t.detect_paths)
        deploy_str = "\n".join(f"{k} → {v}" for k, v in t.deploy_map.items())
        table.add_row(t.name, detect_str, deploy_str)
    console.print(table)

    console.print(
        "\n[dim]Run [bold]mnemos util-setup --target all[/bold] "
        "to deploy the integration pack.[/dim]"
    )


@util_app.command(name="setup")
def setup_cmd(
    target: Annotated[
        str,
        typer.Option(
            "--target",
            "-t",
            help="Target harness: all | gcw | generic-copilot | cursor (default: all detected)",
        ),
    ] = "all",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be deployed without writing"),
    ] = False,
    no_mcp: Annotated[
        bool,
        typer.Option("--no-mcp", help="Skip MCP server registration"),
    ] = False,
    mnemos_bin: Annotated[
        str | None,
        typer.Option("--mnemos-bin", help="Path to mnemos executable for MCP registration"),
    ] = None,
) -> None:
    """Deploy instructions + skills + prompts and register MCP (unified setup).

    This is the single entry point: running ``mnemos util-setup`` wires
    everything — file deployment and MCP registration — in one pass.
    Idempotent: re-running updates stale files without duplicating.
    """
    targets = _resolve_targets(target)
    if not targets:
        return

    mgr = _manager()
    any_failed = False

    for name in targets:
        if dry_run:
            console.print(f"[cyan][dry-run][/cyan] Would set up target: [bold]{name}[/bold]")
        else:
            console.print(f"Setting up target: [bold]{name}[/bold]")

        result = mgr.setup(
            name,
            dry_run=dry_run,
            register_mcp=not no_mcp,
            mnemos_bin=mnemos_bin,
        )
        _print_deploy_result(result, dry_run=dry_run)

        if result.mcp_note and not result.mcp_registered and not no_mcp and not dry_run:
            any_failed = True

    if any_failed:
        console.print("\n[yellow]⚠ Some steps had issues — see above.[/yellow]")
        raise typer.Exit(1)

    console.print("\n[green]✓[/green] Setup complete.")


@util_app.command(name="update")
def update_cmd(
    target: Annotated[
        str,
        typer.Option("--target", "-t", help="Target: all | <name> (default: all detected)"),
    ] = "all",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be updated without writing"),
    ] = False,
) -> None:
    """Update already-deployed files to the current package version.

    Uses the version stamp to detect stale files. Only files carrying an
    outdated mnemos-integration stamp are touched.
    """
    targets = _resolve_targets(target)
    if not targets:
        return

    mgr = _manager()
    for name in targets:
        console.print(f"Updating target: [bold]{name}[/bold]")
        result = mgr.update(name, dry_run=dry_run)
        _print_deploy_result(result, dry_run=dry_run)

    console.print("\n[green]✓[/green] Update complete.")


@util_app.command(name="verify")
def verify_cmd(
    target: Annotated[
        str,
        typer.Option("--target", "-t", help="Target: all | <name> (default: all detected)"),
    ] = "all",
) -> None:
    """Compare deployed files against the shipped pack.

    Reports: installed (version), stale, missing. Exits 0 if all current,
    exits 1 if any stale or missing files are found.
    """
    targets = _resolve_targets(target)
    if not targets:
        return

    mgr = _manager()
    has_issues = False

    for name in targets:
        result = mgr.verify(name)
        _print_verify_result(result)

        if result.stale_count > 0 or result.missing_count > 0:
            has_issues = True
            console.print(
                f"  [yellow]{result.stale_count} stale, {result.missing_count} missing[/yellow]"
            )

    if has_issues:
        console.print(
            "\n[yellow]⚠ Stale or missing files detected. "
            "Run [bold]mnemos util-update[/bold] to fix.[/yellow]"
        )
        raise typer.Exit(1)
    else:
        console.print("\n[green]✓[/green] All files current.")


@util_app.command(name="uninstall")
def uninstall_cmd(
    target: Annotated[
        str,
        typer.Option("--target", "-t", help="Target: all | <name> (default: all detected)"),
    ] = "all",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be removed without deleting"),
    ] = False,
) -> None:
    """Remove ONLY files carrying the mnemos-integration version stamp.

    User-created files are never deleted. Lists what was removed (or would
    be removed with ``--dry-run``).
    """
    targets = _resolve_targets(target)
    if not targets:
        return

    mgr = _manager()
    total_removed = 0
    total_skipped = 0

    for name in targets:
        prefix = "[dry-run] " if dry_run else ""
        console.print(f"{prefix}Uninstalling from target: [bold]{name}[/bold]")
        result = mgr.uninstall(name, dry_run=dry_run)

        if result.removed:
            console.print(f"  [green]Removed ({len(result.removed)}):[/green]")
            for p in result.removed:
                console.print(f"    ✗ {p}")
            total_removed += len(result.removed)
        else:
            console.print("  [dim]No stamped files found.[/dim]")

        if result.skipped_user_files:
            console.print(f"  [dim]Skipped user files ({len(result.skipped_user_files)}):[/dim]")
            for p in result.skipped_user_files[:10]:
                console.print(f"    ✓ {p} (no stamp)")
            if len(result.skipped_user_files) > 10:
                console.print(f"    ... and {len(result.skipped_user_files) - 10} more")
            total_skipped += len(result.skipped_user_files)

    prefix = "[dry-run] " if dry_run else ""
    console.print(
        f"\n{prefix}[green]✓[/green] Uninstall complete: "
        f"{total_removed} files removed, {total_skipped} user files preserved."
    )
