"""CLI interface for AI-Brain."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ai_brain.config import load_settings
from ai_brain.ingestion import IngestionPipeline
from ai_brain.manager import MemoryManager
from ai_brain.models import MemoryCreate, MemorySource, MemoryType, SearchQuery

app = typer.Typer(
    name="brain",
    help="AI-Brain — hybrid long-term memory system",
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


# ── add ───────────────────────────────────────────────────────────────────


@app.command()
def add(
    content: str = typer.Argument(None, help="Text content to remember"),
    title: str = typer.Option(None, "--title", "-t", help="Title for the memory"),
    tags: str = typer.Option("", "--tags", "-T", help="Comma-separated tags"),
    file: Path = typer.Option(None, "--file", "-f", help="Import from file"),
    url: str = typer.Option(None, "--url", "-u", help="Import from URL"),
    source: MemorySource = typer.Option(MemorySource.CLI, "--source", "-s"),
    memory_type: MemoryType = typer.Option(MemoryType.NOTE, "--type"),
    config: str = ConfigOption,
) -> None:
    """Add a new memory."""
    mgr = get_manager(config)
    ingestion = IngestionPipeline()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    if url:
        with console.status("Fetching URL..."):
            data = ingestion.from_url(url, tag_list)
    elif file:
        data = ingestion.from_file(file, tag_list)
    elif content:
        data = MemoryCreate(
            content=content,
            title=title,
            tags=tag_list,
            source=source,
            memory_type=memory_type,
        )
    else:
        # Read from stdin
        stdin_text = sys.stdin.read().strip()
        if not stdin_text:
            console.print("[red]No content provided. Use argument, --file, --url, or pipe to stdin.[/red]")
            raise typer.Exit(1)
        data = MemoryCreate(content=stdin_text, title=title, tags=tag_list, source=source)

    with console.status("Embedding..."):
        memory = mgr.add(data)

    console.print(Panel(
        f"[bold]{memory.auto_title()}[/bold]\n"
        f"ID: [dim]{memory.id}[/dim]\n"
        f"Tags: {', '.join(memory.tags) or '—'}\n"
        f"File: [dim]{memory.file_path}[/dim]",
        title="[green]✓ Memory added[/green]",
        border_style="green",
    ))


# ── search ────────────────────────────────────────────────────────────────


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    tags: str = typer.Option("", "--tags", "-T", help="Filter by tags (comma-separated)"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results"),
    source: MemorySource = typer.Option(None, "--source", "-s"),
    memory_type: MemoryType = typer.Option(None, "--type"),
    config: str = ConfigOption,
) -> None:
    """Search memories (hybrid: semantic + full-text)."""
    mgr = get_manager(config)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    sq = SearchQuery(
        query=query,
        tags=tag_list,
        source=source,
        memory_type=memory_type,
        limit=limit,
    )

    with console.status("Searching..."):
        results = mgr.search(sq)

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return

    table = Table(title=f"Search results for: {query}", show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Score", width=6)
    table.add_column("Type", width=6)
    table.add_column("Title", min_width=30)
    table.add_column("Tags", width=20)
    table.add_column("ID", style="dim", width=10)

    for i, result in enumerate(results, 1):
        m = result.memory
        table.add_row(
            str(i),
            f"{result.score:.3f}",
            result.search_type[:3],
            m.auto_title()[:60],
            ", ".join(m.tags)[:20] or "—",
            m.id[:8],
        )

    console.print(table)


# ── list ──────────────────────────────────────────────────────────────────


@app.command(name="list")
def list_cmd(
    limit: int = typer.Option(20, "--limit", "-n"),
    source: MemorySource = typer.Option(None, "--source", "-s"),
    memory_type: MemoryType = typer.Option(None, "--type"),
    tags: str = typer.Option("", "--tags", "-T"),
    config: str = ConfigOption,
) -> None:
    """List recent memories."""
    mgr = get_manager(config)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    memories = mgr.list_memories(limit=limit, source=source, memory_type=memory_type, tags=tag_list)

    if not memories:
        console.print("[yellow]No memories found.[/yellow]")
        return

    table = Table(title="Memories", show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Title", min_width=30)
    table.add_column("Tags", width=20)
    table.add_column("Source", width=8)
    table.add_column("Created", width=12)
    table.add_column("ID", style="dim", width=10)

    for i, m in enumerate(memories, 1):
        table.add_row(
            str(i),
            m.auto_title()[:60],
            ", ".join(m.tags)[:20] or "—",
            m.source.value,
            m.created_at.strftime("%Y-%m-%d"),
            m.id[:8],
        )

    console.print(table)


# ── get ───────────────────────────────────────────────────────────────────


@app.command()
def get(
    memory_id: str = typer.Argument(..., help="Memory ID (full or prefix)"),
    config: str = ConfigOption,
) -> None:
    """Get a single memory by ID."""
    mgr = get_manager(config)
    memory = mgr.get(memory_id)
    if not memory:
        console.print(f"[red]Memory {memory_id} not found[/red]")
        raise typer.Exit(1)

    console.print(Panel(
        f"[bold]{memory.auto_title()}[/bold]\n\n"
        f"{memory.content[:500]}{'...' if len(memory.content) > 500 else ''}\n\n"
        f"[dim]Tags: {', '.join(memory.tags) or '—'}[/dim]\n"
        f"[dim]Source: {memory.source.value}[/dim]\n"
        f"[dim]Type: {memory.memory_type.value}[/dim]\n"
        f"[dim]Created: {memory.created_at.isoformat()}[/dim]\n"
        f"[dim]File: {memory.file_path or '—'}[/dim]",
        title=f"Memory [{memory.id[:8]}]",
    ))


# ── delete ────────────────────────────────────────────────────────────────


@app.command()
def delete(
    memory_id: str = typer.Argument(..., help="Memory ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    config: str = ConfigOption,
) -> None:
    """Delete a memory."""
    mgr = get_manager(config)
    if not force:
        memory = mgr.get(memory_id)
        if not memory:
            console.print(f"[red]Memory {memory_id} not found[/red]")
            raise typer.Exit(1)
        if not typer.confirm(f"Delete '{memory.auto_title()}'?"):
            raise typer.Abort()

    if mgr.delete(memory_id):
        console.print(f"[green]✓ Deleted {memory_id[:8]}[/green]")
    else:
        console.print(f"[red]Memory {memory_id} not found[/red]")


# ── tags ──────────────────────────────────────────────────────────────────


@app.command()
def tags(config: str = ConfigOption) -> None:
    """List all tags with counts."""
    mgr = get_manager(config)
    tag_map = mgr.get_tags()

    if not tag_map:
        console.print("[yellow]No tags found.[/yellow]")
        return

    table = Table(title="Tags")
    table.add_column("Tag")
    table.add_column("Count", justify="right")

    for tag, count in tag_map.items():
        table.add_row(tag, str(count))

    console.print(table)


# ── sync ──────────────────────────────────────────────────────────────────


@app.command()
def sync(config: str = ConfigOption) -> None:
    """Sync Obsidian vault — index all markdown files."""
    mgr = get_manager(config)
    with console.status("Syncing vault..."):
        result = mgr.sync_vault()
    console.print(
        f"[green]✓ Vault synced:[/green] "
        f"{result['added']} added, {result['updated']} updated, {result['total']} total"
    )


# ── stats ─────────────────────────────────────────────────────────────────


@app.command()
def stats(config: str = ConfigOption) -> None:
    """Show statistics."""
    mgr = get_manager(config)
    s = mgr.stats()
    console.print(Panel(
        f"Total memories: [bold]{s['total_memories']}[/bold]\n"
        f"Total embeddings: [bold]{s['total_embeddings']}[/bold]\n"
        f"Vault: {s['vault_path']}\n"
        f"Data: {s['data_dir']}",
        title="AI-Brain Stats",
    ))


# ── serve ─────────────────────────────────────────────────────────────────


@app.command()
def serve(
    host: str = typer.Option(None, "--host", "-H"),
    port: int = typer.Option(None, "--port", "-p"),
    config: str = ConfigOption,
) -> None:
    """Start the REST API server with Web UI at http://host:port/"""
    import uvicorn

    settings = load_settings(config)
    h = host or settings.api.host
    p = port or settings.api.port
    console.print(f"[green]Starting AI-Brain server at http://{h}:{p}/[/green]")
    uvicorn.run(
        "ai_brain.api:app",
        host=h,
        port=p,
        reload=False,
    )


# ── watch ─────────────────────────────────────────────────────────────────


@app.command()
def watch(
    paths: list[Path] = typer.Argument(None, help="Directories to watch"),
    no_scan: bool = typer.Option(False, "--no-scan", help="Skip initial directory scan"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging"),
    config: str = ConfigOption,
) -> None:
    """Watch directories and auto-index files into brain memory."""
    import logging

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(message)s")

    settings = load_settings(config)
    mgr = get_manager(config)

    from ai_brain.watcher import BrainWatcher

    watch_paths = [Path(p) for p in (paths or [])]
    # Merge with config paths
    for cp in settings.watcher.paths:
        p = Path(cp).expanduser()
        if p not in watch_paths:
            watch_paths.append(p)

    if not watch_paths:
        console.print(
            "[red]No paths to watch. Provide paths as arguments or set watcher.paths in config.yaml[/red]"
        )
        raise typer.Exit(1)

    ignore = set(settings.watcher.ignore_dirs)
    extensions = {e if e.startswith(".") else f".{e}" for e in settings.watcher.extensions}

    watcher = BrainWatcher(
        manager=mgr,
        ignore_dirs=ignore,
        extensions=extensions,
        max_file_size=settings.watcher.max_file_size_kb * 1024,
    )

    console.print(f"[green]Watching {len(watch_paths)} directories[/green]")
    for p in watch_paths:
        console.print(f"  [dim]{p}[/dim]")

    watcher.run_forever(watch_paths, initial_scan=not no_scan)

    s = watcher.stats
    console.print(
        f"\n[green]Done.[/green] Ingested: {s['ingested']}, "
        f"Updated: {s['updated']}, Skipped: {s['skipped']}, Errors: {s['errors']}"
    )


if __name__ == "__main__":
    app()
