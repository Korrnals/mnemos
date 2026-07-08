"""Mnemos CLI — Typer-based command interface.

Entry point: mnemos (declared in pyproject.toml [project.scripts]).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.table import Table

from mnemos.cli._manager import get_manager
from mnemos.config import load_settings
from mnemos.logging_setup import setup_logging
from mnemos.models import MemoryCreate, MemorySource, MemoryType

# ``get_manager`` lives in the leaf module ``_manager`` so that CLI
# subcommand modules (export_cmd, import_cmd) can import it without forming
# a circular import with this module (which imports them to register the
# Typer sub-apps). Re-exported here for backward compatibility.

if TYPE_CHECKING:
    from mnemos.api.auth_store import AuthStore

app = typer.Typer(
    name="mnemos",
    help="Mnemos — standalone memory & knowledge server for GCW agents.",
    no_args_is_help=True,
)
console = Console()

# Module-level verbose flag — set by the top-level callback via ``--verbose``
# so that subcommands (serve, mcp-server) can pick it up without re-parsing.
_verbose: bool = False


def _version_callback(value: bool) -> None:
    if value:
        from mnemos import __version__

        console.print(f"mnemos {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Show the Mnemos version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Enable DEBUG logging (overrides config log level).",
        ),
    ] = False,
) -> None:
    """Mnemos — standalone memory & knowledge server for GCW agents."""
    global _verbose
    _verbose = verbose


ConfigOption = typer.Option(None, "--config", "-c", help="Path to config.yaml")


# ── add ────────────────────────────────────────────────────────────────────────


@app.command()
def add(
    content: str = typer.Argument(None, help="Text content to remember"),
    title: str = typer.Option(None, "--title", "-t"),
    tags: str = typer.Option("", "--tags", "-T", help="Comma-separated tags"),
    file: Annotated[Path | None, typer.Option("--file", "-f", help="Import from file")] = None,
    url: str = typer.Option(None, "--url", "-u", help="Import from URL"),
    source: Annotated[MemorySource, typer.Option("--source", "-s")] = MemorySource.CLI,
    memory_type: Annotated[MemoryType, typer.Option("--type")] = MemoryType.NOTE,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Show context-filter stats (profile, token reduction, dedup, noise) "
            "without saving the memory.",
        ),
    ] = False,
    config: str = ConfigOption,
) -> None:
    """Add a new memory entry.

    With ``--dry-run``: validates the tag contract, runs the context filter
    pipeline on the content, and prints filter stats **without saving**.
    Useful for previewing how the M10 Context Filter will transform input
    before committing it to the store.
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    # ── --dry-run: validate tags + run filter, then exit without saving ──
    if dry_run:
        if url:
            console.print(
                "[red]--dry-run is not supported with --url (content is fetched at "
                "ingest time).[/red]"
            )
            raise typer.Exit(1)
        if file:
            text = Path(file).read_text(encoding="utf-8")
        elif content:
            text = content
        else:
            stdin_text = sys.stdin.read().strip()
            if not stdin_text:
                console.print("[red]No content provided.[/red]")
                raise typer.Exit(1)
            text = stdin_text

        # Validate tag contract (raises TagContractError in strict mode).
        from mnemos.config import load_settings as _load_settings
        from mnemos.filter.pipeline import apply_filter
        from mnemos.models import validate_tag_contract

        settings = _load_settings(config)
        validate_tag_contract(tag_list, strict=settings.mnemos.strict_tag_contract)

        result = apply_filter(text)
        stats = result["stats"]
        tokens_in = len(text) // 4 or 1
        tokens_out = stats["tokens"]["estimated_tokens"]
        reduction_pct = round((1 - tokens_out / tokens_in) * 100, 1) if tokens_in else 0.0

        console.print("[cyan][dry-run][/cyan] Filter preview (no memory saved):")
        console.print(f"  Input:     {tokens_in} tokens")
        console.print(f"  Output:    {tokens_out} tokens ({reduction_pct}% reduction)")
        console.print(f"  Profile:   {result['profile']} (auto-detected)")
        dedup = stats.get("dedup", {})
        console.print(
            f"  Dedup:     {dedup.get('exact_dups', 0)} exact, "
            f"{dedup.get('near_dups', 0)} near-duplicates removed"
        )
        noise = stats.get("noise", {})
        noise_lines = (
            noise.get("removed_ansi", 0)
            + noise.get("removed_progress", 0)
            + noise.get("removed_timestamps", 0)
            + noise.get("removed_separators", 0)
        )
        console.print(f"  Noise:     {noise_lines} lines cleaned")
        budget = stats["tokens"].get("budget")
        console.print(f"  Budget:    {budget if budget else 'not set (no truncation)'}")
        console.print("[dim][dry-run] Memory would be saved with these filter stats.[/dim]")
        return

    mgr = get_manager(config)

    if url:
        project = next((t[len("project:") :] for t in tag_list if t.startswith("project:")), "")
        agent = next((t[len("agent:") :] for t in tag_list if t.startswith("agent:")), "")
        with console.status("Fetching URL..."):
            memory = mgr.ingest_url(url, tags=tag_list, project=project, agent=agent)
    elif file:
        text = Path(file).read_text()
        data = MemoryCreate(content=text, title=title, tags=tag_list, source=source)
        project = next((t[len("project:") :] for t in tag_list if t.startswith("project:")), "")
        agent = next((t[len("agent:") :] for t in tag_list if t.startswith("agent:")), "")
        memory = mgr.add(data, project=project, agent=agent)
    elif content:
        data = MemoryCreate(
            content=content, title=title, tags=tag_list, source=source, memory_type=memory_type
        )
        project = next((t[len("project:") :] for t in tag_list if t.startswith("project:")), "")
        agent = next((t[len("agent:") :] for t in tag_list if t.startswith("agent:")), "")
        memory = mgr.add(data, project=project, agent=agent)
    else:
        stdin_text = sys.stdin.read().strip()
        if not stdin_text:
            console.print("[red]No content provided.[/red]")
            raise typer.Exit(1)
        data = MemoryCreate(content=stdin_text, title=title, tags=tag_list, source=source)
        memory = mgr.add(data)

    console.print(f"[green]✓[/green] Saved: {memory.auto_title()} ({memory.id})")


# ── search ─────────────────────────────────────────────────────────────────────


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(10, "--limit", "-l", help="Max results"),
    project: str = typer.Option(None, "--project", "-p", help="Filter by project slug"),
    tags: str = typer.Option(None, "--tags", "-T", help="Comma-separated tags to filter by"),
    include_raw: bool = typer.Option(
        False,
        "--include-raw",
        help="Include raw/processing entries (skipped by default).",
    ),
    status: str = typer.Option(
        None,
        "--status",
        help=(
            "Filter by status (raw/processing/processed/published/archived). "
            "Takes precedence over --include-raw."
        ),
    ),
    config: str = ConfigOption,
) -> None:
    """Search long-term memory (hybrid FTS + vector)."""
    from mnemos.models import MemoryStatus

    mgr = get_manager(config)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    status_enum = MemoryStatus(status) if status else None
    results = mgr.search(
        query=query,
        tags=tag_list,
        project=project,
        limit=limit,
        include_raw=include_raw,
        status=status_enum,
    )
    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return
    table = Table("Score", "Title", "Tags", "Status")
    for r in results:
        table.add_row(
            f"{r.score:.3f}",
            r.memory.auto_title(),
            ", ".join(r.memory.tags[:5]),
            r.memory.status,
        )
    console.print(table)


# ── recall ─────────────────────────────────────────────────────────────────────


@app.command()
def recall(
    project: str = typer.Option(None, "--project", "-p"),
    agent: str = typer.Option(None, "--agent", "-a", help="Filter by agent slug (M3)"),
    limit: int = typer.Option(10, "--limit", "-l"),
    config: str = ConfigOption,
) -> None:
    """Recall recent memories, optionally filtered by project or agent."""
    from mnemos.models import AgentRecallQuery

    mgr = get_manager(config)
    if agent:
        results = mgr.agent_recall(AgentRecallQuery(agent=agent, project=project, limit=limit))
        memories = [r.memory for r in results]
    else:
        memories = mgr.recall_context(project=project or "", limit=limit)

    if not memories:
        console.print("[yellow]No memories found.[/yellow]")
        return
    for m in memories:
        console.print(f"[cyan]{m.auto_title()}[/cyan]  ({m.id[:8]}…)")
        console.print(f"  tags: {', '.join(m.tags)}")
        console.print()


# ── tags (M2) ─────────────────────────────────────────────────────────────────
# Subcommand tree:
#   mnemos tags validate <vault>   — validate tag contract across a vault

_tags_app = typer.Typer(name="tags", help="Manage and validate memory tags.", no_args_is_help=True)
app.add_typer(_tags_app, name="tags")


@_tags_app.command(name="validate")
def tags_validate(
    vault: Annotated[Path, typer.Argument(help="Path to Mnemos vault directory")],
    config: str = ConfigOption,
) -> None:
    """Validate tag contract across an existing vault. Reports non-conformant entries."""

    console.print(f"[bold]Validating tag contract in:[/bold] {vault}")
    # TODO (M2): scan SQLite + vault markdown files
    console.print(
        "[yellow]Full vault scan not yet implemented (M2 storage layer pending).[/yellow]"
    )


@_tags_app.command(name="normalize")
def tags_normalize(
    config: str = ConfigOption,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview changes without writing to the database."),
    ] = False,
) -> None:
    """Normalize project:/agent: tag case to lowercase across all memories.

    Scans every memory in the SQLite store, lowercases the slug portion of
    project: and agent: tags and replaces spaces with hyphens (matching the
    lax-mode normalization in ``validate_tag_contract``), and updates
    memories that changed via ``update_fields`` (plain UPDATE). The FTS5
    ``AFTER UPDATE`` trigger fires on UPDATE so the search index stays
    consistent; using ``save()`` (INSERT OR REPLACE) here would risk
    desyncing the FTS5 external content table. The denormalised ``project``
    and ``agent`` columns are updated in the same statement.
    """

    mgr = get_manager(config)
    # Page through all memories — list_all paginates with limit/offset.
    page_size = 500
    offset = 0
    scanned = 0
    normalized_count = 0
    changed_projects: set[str] = set()
    changed_agents: set[str] = set()

    while True:
        batch = mgr.sqlite.list_all(limit=page_size, offset=offset)
        if not batch:
            break
        offset += len(batch)

        for mem in batch:
            scanned += 1
            new_tags = list(mem.tags)
            modified = False

            for i, tag in enumerate(new_tags):
                for prefix in ("project:", "agent:"):
                    if tag.startswith(prefix):
                        slug = tag[len(prefix) :]
                        # Match validate_tag_contract lax-mode normalization:
                        # strip, lowercase AND replace spaces with hyphens.
                        # Without the strip step, `project:My Project ` would
                        # become `project:-my-project-` (leading/trailing
                        # hyphens from unstripped spaces). Without the
                        # space→hyphen step the CLI diverged from the
                        # contract, leaving `project:My Project` as
                        # `project:my project` (invalid slug with a space).
                        lower = slug.strip().lower().replace(" ", "-")
                        if lower != slug:
                            new_tags[i] = prefix + lower
                            modified = True
                            if prefix == "project:":
                                changed_projects.add(f"{slug} → {lower}")
                            else:
                                changed_agents.add(f"{slug} → {lower}")

            if not modified:
                continue

            normalized_count += 1
            if dry_run:
                continue

            # Use update_fields (UPDATE) not save() (INSERT OR REPLACE).
            # INSERT OR REPLACE can desync the FTS5 external content table
            # (`content=memories`), causing "missing row from content
            # table" errors on subsequent searches. A plain UPDATE fires
            # the `memories_au` AFTER UPDATE trigger which keeps the FTS5
            # index consistent. Also update the denormalised `project` and
            # `agent` columns so per-project / per-agent queries stay in
            # sync with the normalized tags.
            new_project = next(
                (t[len("project:") :] for t in new_tags if t.startswith("project:")),
                mem.project,
            )
            new_agent = next(
                (t[len("agent:") :] for t in new_tags if t.startswith("agent:")),
                mem.agent,
            )
            mgr.sqlite.update_fields(
                mem.id,
                tags=new_tags,
                project=new_project,
                agent=new_agent,
            )

    console.print(f"[bold]Scanned:[/bold] {scanned} memories")
    console.print(f"[bold]Normalized:[/bold] {normalized_count} memories")
    if dry_run:
        console.print("[yellow](dry-run — no changes written)[/yellow]")
    if changed_projects:
        console.print("\n[bold]Changed project slugs:[/bold]")
        for entry in sorted(changed_projects):
            console.print(f"  {entry}")
    if changed_agents:
        console.print("\n[bold]Changed agent slugs:[/bold]")
        for entry in sorted(changed_agents):
            console.print(f"  {entry}")


# ── stats ──────────────────────────────────────────────────────────────────────


@app.command()
def stats(config: str = ConfigOption) -> None:
    """Display Mnemos health statistics."""
    mgr = get_manager(config)
    s = mgr.stats()
    for k, v in s.items():
        console.print(f"  [bold]{k}[/bold]: {v}")


@app.command(name="fts")
def fts_cmd(
    action: str = typer.Argument(..., help="Action: rebuild"),
    config: str = ConfigOption,
) -> None:
    """FTS5 index management."""
    mgr = get_manager(config)
    if action == "rebuild":
        count = mgr.sqlite.rebuild_fts_index()
        console.print(f"[green]✓ FTS5 index rebuilt: {count} rows indexed[/green]")
    else:
        console.print(f"[red]Unknown action: {action}. Use 'rebuild'.[/red]")
        raise typer.Exit(1)
    mgr.close()


@app.command(name="processor")
def processor_cmd(
    action: str = typer.Argument(..., help="Action: status|run|start|stop"),
    config: str = ConfigOption,
) -> None:
    """Background processor management."""
    mgr = get_manager(config)
    if action == "status":
        s = mgr.stats()
        proc = s.get("processor", {})
        console.print(f"  queue_depth: {proc.get('queue_depth', 'N/A')}")
        console.print(f"  last_processed_at: {proc.get('last_processed_at', 'N/A')}")
        console.print(f"  running: {mgr.processor_running}")
    elif action == "run":
        result = mgr.run_pipeline()
        console.print(f"  clusters: {result['clusters']}")
        console.print(f"  synthesized: {result['synthesized']}")
        console.print(f"  published: {result['published']}")
        console.print(f"  failed_quality_gate: {result['failed_quality_gate']}")
    elif action == "start":
        mgr.start_background_processor()
        console.print("[green]✓ Background processor started[/green]")
    elif action == "stop":
        mgr.stop_background_processor()
        console.print("[green]✓ Background processor stopped[/green]")
    else:
        console.print(f"[red]Unknown action: {action}. Use: status|run|start|stop[/red]")
        raise typer.Exit(1)
    mgr.close()


# ── reindex ───────────────────────────────────────────────────────────────────


@app.command(name="reindex")
def reindex_cmd(
    batch_size: int = typer.Option(100, "--batch-size", "-b", help="Batch size for embedding"),
    config: str = ConfigOption,
) -> None:
    """Rebuild the vector index for all published memories.

    Re-embeds every published memory and upserts into the vector store.
    Use after enabling embeddings or switching embedding models.
    """
    mgr = get_manager(config)
    result = mgr.rebuild_vector_index(batch_size=batch_size)
    console.print(f"  [cyan]total: {result['total']}[/cyan]")
    console.print(f"  [green]indexed: {result['indexed']}[/green]")
    console.print(f"  [red]failed: {result['failed']}[/red]")
    mgr.close()


# ── filter (M10) ───────────────────────────────────────────────────────────────


@app.command(name="filter")
def filter_cmd(
    memory_id: str = typer.Argument(
        None,
        help=(
            "Memory ID to filter. Run context filter on a single memory: "
            "shows clean content + reduction stats. Omit with --all to re-filter every memory."
        ),
    ),
    profile: str = typer.Option(
        None,
        "--profile",
        "-p",
        help="log|terminal|code|docs|web|default (auto-detected if omitted)",
    ),
    budget: int = typer.Option(None, "--budget", "-b", help="Token budget for truncation"),
    all_memories: bool = typer.Option(
        False,
        "--all",
        help=(
            "Re-run context filter on ALL memories. Existing clean_content is "
            "overwritten with fresh filter output. Reports aggregate stats."
        ),
    ),
    config: str = ConfigOption,
) -> None:
    """Run the Context Filter on a memory. Shows clean content + reduction stats.

    Note: re-filtering with a different profile produces different clean_content.
    The filter is idempotent only when the same profile is used.
    """
    mgr = get_manager(config)

    if all_memories:
        with console.status("[bold green]Re-filtering all memories..."):
            summary = mgr.filter_all(profile=profile, budget=budget)
        console.print(f"[green]✓[/green] Filtered: {summary['filtered']}")
        console.print(f"  total:   {summary['total']}")
        console.print(f"  failed:  {summary['failed']}")
        console.print(f"  skipped: {summary['skipped']}")
        return

    if not memory_id:
        console.print("[red]Provide a memory ID or use --all.[/red]")
        raise typer.Exit(1)

    result = mgr.apply_context_filter(memory_id, profile=profile, budget=budget)
    if result.get("status") == "error":
        console.print(f"[red]✗[/red] {result.get('error', 'unknown error')}")
        raise typer.Exit(1)

    console.print(f"[green]✓[/green] Filtered: {memory_id}")
    console.print(f"  profile: {result['filter_profile']}")
    stats_data = result.get("stats", {})
    if "dedup" in stats_data:
        console.print(f"  dedup:   {stats_data['dedup']}")
    if "tokens" in stats_data:
        console.print(f"  tokens:  {stats_data['tokens']}")
    console.print(f"  clean_content:\n{result['clean_content']}")


# ── serve ──────────────────────────────────────────────────────────────────────


@app.command()
def serve(
    host: str = typer.Option(None, "--host"),
    port: int = typer.Option(None, "--port"),
    log_file: Annotated[
        Path | None,
        typer.Option("--log-file", help="Override config log file path (enables file logging)."),
    ] = None,
    config: str = ConfigOption,
) -> None:
    """Start the Mnemos HTTP API server."""
    import os

    import uvicorn

    settings = load_settings(config)
    if log_file is not None:
        settings.logging.log_file = log_file
        settings.resolve_paths()
    setup_logging(settings, verbose=_verbose)
    h = host or settings.api.host
    p = port or settings.api.port
    # Propagate effective bind to the app process so the startup guard and
    # AuthMiddleware see the real host/port (CLI overrides must reach
    # load_settings() inside the worker - finding auth-1).
    os.environ["MNEMOS_API__HOST"] = h
    os.environ["MNEMOS_API__PORT"] = str(p)
    uvicorn.run(
        "mnemos.api.main:app",
        host=h,
        port=p,
        workers=settings.runtime.uvicorn_workers,
    )


# ── mcp-server ─────────────────────────────────────────────────────────────────


@app.command(name="mcp-server")
def mcp_server_cmd(config: str = ConfigOption) -> None:
    """Start the MCP server (stdio transport)."""
    import asyncio

    from mnemos.mcp_server import main as mcp_main

    settings = load_settings(config)
    setup_logging(settings, verbose=_verbose)
    asyncio.run(mcp_main())


# ── migrate (M13) ──────────────────────────────────────────────────────────────
# Subcommand tree:
#   mnemos migrate from-ai-brain   — migrate ai-brain data to Mnemos format
#   mnemos migrate tags            — migrate gcw: tags → mnemos: tags

_migrate_app = typer.Typer(
    name="migrate", help="Migrate data from other memory systems.", no_args_is_help=True
)
app.add_typer(_migrate_app, name="migrate")

_DEFAULT_AI_BRAIN_SOURCE = Path("~/.ai-brain").expanduser()
_DEFAULT_BRAIN_VAULT = Path("~/brain-vault").expanduser()


@_migrate_app.command(name="from-ai-brain")
def migrate(
    source: Annotated[
        Path, typer.Option("--source", help="ai-brain data dir")
    ] = _DEFAULT_AI_BRAIN_SOURCE,
    vault: Annotated[
        Path, typer.Option("--vault", help="ai-brain vault dir")
    ] = _DEFAULT_BRAIN_VAULT,
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be migrated"),
    config: str = ConfigOption,
) -> None:
    """Migrate existing ai-brain data to Mnemos format. (M13)"""
    from mnemos.cli.migrate import migrate_from_ai_brain

    settings = load_settings(config)
    db_path = source / "ai_brain.db"
    vault_path = vault if vault.exists() else None

    with console.status("[bold green]Migrating ai-brain → Mnemos..."):
        summary = migrate_from_ai_brain(
            db_path,
            vault_path,
            dry_run=dry_run,
            settings=settings,
        )

    console.print(f"[green]✓[/green] Memories migrated: {summary['memories_migrated']}")
    if vault_path:
        console.print(f"[green]✓[/green] Vault files migrated: {summary['vault_files_migrated']}")
    if summary["errors"]:
        console.print(f"[yellow]⚠[/yellow] Errors: {len(summary['errors'])}")
    if dry_run:
        console.print("[cyan]Dry run — no changes written.[/cyan]")


@_migrate_app.command(name="tags")
def migrate_tags(
    config: str = ConfigOption,
) -> None:
    """Migrate legacy gcw: tags to mnemos: tags in the database.

    Converts all ``gcw:<subtype>`` tags in existing memories to
    ``mnemos:<subtype>``. Idempotent — safe to run multiple times.
    """
    from mnemos.cli.migrate import migrate_gcw_to_mnemos_tags

    settings = load_settings(config)
    settings.resolve_paths()
    settings.apply_runtime_env()
    db_path = settings.db_path

    if not db_path.exists():
        console.print(f"[red]✗[/red] Database not found: {db_path}")
        raise typer.Exit(1)

    with console.status("[bold green]Migrating gcw: → mnemos: tags..."):
        summary = migrate_gcw_to_mnemos_tags(db_path)

    console.print(f"[green]✓[/green] Memories updated: {summary['memories_updated']}")
    console.print(f"[green]✓[/green] Tags converted: {summary['tags_converted']}")
    if summary["memories_updated"] == 0:
        console.print("[cyan]No gcw: tags found — nothing to migrate.[/cyan]")


# ── auth (T-AUTH, ADR-0014) ───────────────────────────────────────────────────
# Subcommand tree:
#   mnemos auth token create [--name <label>] [--expires <iso8601>]
#   mnemos auth token list
#   mnemos auth token revoke <token_id>
#   mnemos auth totp enroll  --token-id <id>
#   mnemos auth totp disable --token-id <id>
#   mnemos auth totp test    --token-id <id> --code <123456>

_auth_app = typer.Typer(
    name="auth", help="Manage API auth tokens and TOTP 2FA.", no_args_is_help=True
)
_token_app = typer.Typer(name="token", help="Manage bearer tokens.", no_args_is_help=True)
_totp_app = typer.Typer(name="totp", help="Manage TOTP 2FA enrollment.", no_args_is_help=True)

app.add_typer(_auth_app, name="auth")
_auth_app.add_typer(_token_app, name="token")
_auth_app.add_typer(_totp_app, name="totp")


def _auth_store(config: str | None = None) -> AuthStore:
    from mnemos.api.auth_store import AuthStore  # lazy: avoids circular deps

    settings = load_settings(config)
    settings.resolve_paths()
    settings.mnemos.data_dir.mkdir(parents=True, exist_ok=True)
    return AuthStore(settings.db_path)


# importing here to satisfy mypy (used in type annotation above)


@_token_app.command("create")
def token_create(
    name: str = typer.Option(None, "--name", "-n", help="Human-readable label"),
    expires: str = typer.Option(None, "--expires", "-e", help="ISO-8601 expiry, e.g. 2027-01-01"),
    no_totp: bool = typer.Option(
        False,
        "--no-totp",
        help="Create a token that can be used directly as a bearer without "
        "the login/verify/session flow (sets totp_required=false). By default "
        "tokens require TOTP.",
    ),
    config: str = ConfigOption,
) -> None:
    """Mint a new bearer token and print it ONCE."""
    store = _auth_store(config)
    try:
        # Normalize expires_at to offset-aware ISO-8601 (UTC).
        # Accepts naive dates like "2027-12-31" and converts to "2027-12-31T00:00:00+00:00".
        normalized_expires: str | None = None
        if expires:
            try:
                from datetime import UTC, datetime

                dt = datetime.fromisoformat(expires)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                normalized_expires = dt.isoformat()
            except ValueError:
                console.print(f"[red]Invalid expiry format: {expires}[/red]")
                console.print(
                    "[dim]Expected ISO-8601, e.g. 2027-01-01 or 2027-01-01T00:00:00[/dim]"
                )
                raise typer.Exit(1) from None
        token_id, plaintext = store.create_token(
            name=name, expires_at=normalized_expires, totp_required=not no_totp
        )
    finally:
        store.close()
    console.print("[green]✓[/green] Token created:")
    console.print(f"  token_id : [bold]{token_id}[/bold]")
    console.print(f"  bearer   : [bold yellow]{plaintext}[/bold yellow]")
    console.print("[red]Store this token now — it will not be shown again.[/red]")


@_token_app.command("list")
def token_list(config: str = ConfigOption) -> None:
    """List all tokens (IDs and metadata — no secrets)."""
    store = _auth_store(config)
    try:
        tokens = store.list_tokens()
    finally:
        store.close()
    if not tokens:
        console.print("[yellow]No tokens found.[/yellow]")
        return
    table = Table("token_id", "name", "created_at", "expires_at", "disabled_at", "totp_required")
    for t in tokens:
        totp_req = t.get("totp_required", 1)
        totp_str = "no" if totp_req is not None and int(str(totp_req)) == 0 else "yes"
        table.add_row(
            str(t.get("token_id", "")),
            str(t.get("name") or ""),
            str(t.get("created_at", "")),
            str(t.get("expires_at") or ""),
            str(t.get("disabled_at") or ""),
            totp_str,
        )
    console.print(table)


@_token_app.command("revoke")
def token_revoke(
    token_id: str = typer.Argument(..., help="token_id to permanently revoke"),
    config: str = ConfigOption,
) -> None:
    """Permanently revoke a token."""
    store = _auth_store(config)
    try:
        ok = store.revoke_token(token_id)
    finally:
        store.close()
    if ok:
        console.print(f"[green]✓[/green] Token {token_id} revoked.")
    else:
        console.print(f"[red]Token {token_id} not found.[/red]")
        raise typer.Exit(1)


@_totp_app.command("enroll")
def totp_enroll(
    token_id: str = typer.Option(..., "--token-id", help="token_id to enroll TOTP for"),
    config: str = ConfigOption,
) -> None:
    """Generate a TOTP secret and print the provisioning URI + optional QR code."""
    import pyotp

    from mnemos.api.auth import encrypt_totp_secret

    settings = load_settings(config)
    master_key = settings.api.totp_master_key.get_secret_value()
    if not master_key:
        console.print(
            "[red]MNEMOS_API__TOTP_MASTER_KEY is not set — cannot encrypt TOTP secret.[/red]"
        )
        raise typer.Exit(1)

    totp_secret = pyotp.random_base32(32)
    totp = pyotp.TOTP(totp_secret)
    uri = totp.provisioning_uri(name="operator", issuer_name="mnemos")

    store = _auth_store(config)
    try:
        row = store.get_token_by_id(token_id)
        if row is None:
            console.print(f"[red]Token {token_id!r} not found.[/red]")
            raise typer.Exit(1)
        encrypted = encrypt_totp_secret(totp_secret, master_key)
        store.set_totp_secret(token_id, encrypted)
    finally:
        store.close()

    console.print(f"[green]✓[/green] TOTP enrolled for {token_id}")
    console.print(f"  otpauth URI: [bold]{uri}[/bold]")
    try:
        import qrcode

        qr = qrcode.QRCode()
        qr.add_data(uri)
        qr.make(fit=True)
        qr.print_ascii()
    except ImportError:
        console.print("  (install qrcode[pil] for ASCII QR display)")


@_totp_app.command("disable")
def totp_disable(
    token_id: str = typer.Option(..., "--token-id", help="token_id to disable TOTP for"),
    config: str = ConfigOption,
) -> None:
    """Remove the TOTP secret from a token (disables 2FA for that token)."""
    store = _auth_store(config)
    try:
        row = store.get_token_by_id(token_id)
        if row is None:
            console.print(f"[red]Token {token_id!r} not found.[/red]")
            raise typer.Exit(1)
        store.clear_totp_secret(token_id)
    finally:
        store.close()
    console.print(f"[green]✓[/green] TOTP disabled for {token_id}.")


@_totp_app.command("test")
def totp_test(
    token_id: str = typer.Option(..., "--token-id", help="token_id to test TOTP for"),
    code: str = typer.Option(..., "--code", help="6-digit TOTP code to verify"),
    config: str = ConfigOption,
) -> None:
    """Verify a TOTP code against the enrolled secret (smoke-test for the operator)."""
    import pyotp

    from mnemos.api.auth import decrypt_totp_secret

    settings = load_settings(config)
    master_key = settings.api.totp_master_key.get_secret_value()
    if not master_key:
        console.print("[red]MNEMOS_API__TOTP_MASTER_KEY is not set.[/red]")
        raise typer.Exit(1)

    store = _auth_store(config)
    try:
        row = store.get_token_by_id(token_id)
        if row is None:
            console.print(f"[red]Token {token_id!r} not found.[/red]")
            raise typer.Exit(1)
        encrypted_blob = row.get("totp_secret_encrypted")
        if not isinstance(encrypted_blob, bytes):
            console.print(f"[red]TOTP not enrolled for {token_id}.[/red]")
            raise typer.Exit(1)
        totp_secret = decrypt_totp_secret(encrypted_blob, master_key)
    finally:
        store.close()

    if totp_secret is None:
        console.print("[red]Failed to decrypt TOTP secret — check master key.[/red]")
        raise typer.Exit(1)

    totp = pyotp.TOTP(totp_secret)
    if totp.verify(code, valid_window=1):
        console.print("[green]✓[/green] Code is valid.")
    else:
        console.print("[red]✗[/red] Code is invalid or expired.")
        raise typer.Exit(1)


# ── integration (integration layer) ────────────────────────────────────────────
# Subcommand tree:
#   mnemos integration detect    — print detected harnesses + deploy paths
#   mnemos integration setup     — deploy files + register MCP (unified entry point)
#   mnemos integration update    — bring stale files to current version
#   mnemos integration verify    — compare deployed files against shipped pack
#   mnemos integration uninstall  — remove only stamped files

from mnemos.cli.util import integration_app  # noqa: E402

app.add_typer(integration_app, name="integration")


# ── completion ─────────────────────────────────────────────────────────────────
# Auto-detect current shell, generate completion script, auto-install into rc.

from mnemos.cli.completion import completion_app  # noqa: E402

app.add_typer(completion_app, name="completion")


# ── doctor ─────────────────────────────────────────────────────────────────────
# Health-check: config + data dir + vault + SQLite + vectors + MCP + integration + tags.

from mnemos.cli.doctor import doctor_app  # noqa: E402

app.add_typer(doctor_app, name="doctor")


# ── export / import / logs (M17 — backup/restore + trace viewer) ──────────────

from mnemos.cli.export_cmd import export_app  # noqa: E402
from mnemos.cli.import_cmd import import_app  # noqa: E402
from mnemos.cli.logs import logs_app  # noqa: E402

app.add_typer(export_app, name="export")
app.add_typer(import_app, name="import")
app.add_typer(logs_app, name="logs")
