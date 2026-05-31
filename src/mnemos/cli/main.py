"""Mnemos CLI — Typer-based command interface.

Renamed from ai-brain's CLI (brain → mnemos).
Entry point: mnemos (declared in pyproject.toml [project.scripts]).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mnemos.config import load_settings
from mnemos.manager import MemoryManager
from mnemos.models import MemoryCreate, MemorySource, MemoryType

app = typer.Typer(
    name="mnemos",
    help="Mnemos — standalone memory & knowledge server for GCW agents.",
    no_args_is_help=True,
)
console = Console()

_manager: MemoryManager | None = None


def get_manager(config: str | None = None) -> MemoryManager:
    global _manager
    if _manager is None:
        settings = load_settings(config)
        _manager = MemoryManager(settings)
    return _manager


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
    config: str = ConfigOption,
) -> None:
    """Add a new memory entry."""
    mgr = get_manager(config)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    if url:
        project = next((t[len("project:"):] for t in tag_list if t.startswith("project:")), "")
        agent = next((t[len("agent:"):] for t in tag_list if t.startswith("agent:")), "")
        with console.status("Fetching URL..."):
            memory = mgr.ingest_url(url, tags=tag_list, project=project, agent=agent)
    elif file:
        text = Path(file).read_text()
        data = MemoryCreate(content=text, title=title, tags=tag_list, source=source)
        project = next((t[len("project:"):] for t in tag_list if t.startswith("project:")), "")
        agent = next((t[len("agent:"):] for t in tag_list if t.startswith("agent:")), "")
        memory = mgr.add(data, project=project, agent=agent)
    elif content:
        data = MemoryCreate(
            content=content, title=title, tags=tag_list, source=source, memory_type=memory_type
        )
        project = next((t[len("project:"):] for t in tag_list if t.startswith("project:")), "")
        agent = next((t[len("agent:"):] for t in tag_list if t.startswith("agent:")), "")
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
    limit: int = typer.Option(10, "--limit", "-l"),
    project: str = typer.Option(None, "--project", "-p"),
    config: str = ConfigOption,
) -> None:
    """Search long-term memory (hybrid FTS + vector)."""
    mgr = get_manager(config)
    results = mgr.search(query=query, project=project, limit=limit)
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


# ── tags validate (M2) ─────────────────────────────────────────────────────────


@app.command(name="tags-validate")
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


# ── stats ──────────────────────────────────────────────────────────────────────


@app.command()
def stats(config: str = ConfigOption) -> None:
    """Display Mnemos health statistics."""
    mgr = get_manager(config)
    s = mgr.stats()
    for k, v in s.items():
        console.print(f"  [bold]{k}[/bold]: {v}")


# ── serve ──────────────────────────────────────────────────────────────────────


@app.command()
def serve(
    host: str = typer.Option(None, "--host"),
    port: int = typer.Option(None, "--port"),
    config: str = ConfigOption,
) -> None:
    """Start the Mnemos HTTP API server."""
    import uvicorn

    settings = load_settings(config)
    h = host or settings.api.host
    p = port or settings.api.port
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

    asyncio.run(mcp_main())

# ── migrate-from-ai-brain (M13) ────────────────────────────────────────────────

_DEFAULT_AI_BRAIN_SOURCE = Path("~/.ai-brain").expanduser()
_DEFAULT_BRAIN_VAULT = Path("~/brain-vault").expanduser()


@app.command(name="migrate-from-ai-brain")
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
    console.print("[bold yellow]Migration tool not yet implemented (M13 pending).[/bold yellow]")
    console.print(f"  source: {source}")
    console.print(f"  vault:  {vault}")
    console.print(f"  dry-run: {dry_run}")
