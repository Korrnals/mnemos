"""Mnemos CLI — Typer-based command interface.

Renamed from ai-brain's CLI (brain → mnemos).
Entry point: mnemos (declared in pyproject.toml [project.scripts]).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.table import Table

from mnemos.config import load_settings
from mnemos.manager import MemoryManager
from mnemos.models import MemoryCreate, MemorySource, MemoryType

if TYPE_CHECKING:
    from mnemos.api.auth_store import AuthStore

app = typer.Typer(
    name="mnemos",
    help="Mnemos — standalone memory & knowledge server for GCW agents.",
    no_args_is_help=True,
)
console = Console()

_manager: MemoryManager | None = None


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
) -> None:
    """Mnemos — standalone memory & knowledge server for GCW agents."""


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


# ── tags (M2) ─────────────────────────────────────────────────────────────────
# Subcommand tree:
#   mnemos tags validate <vault>   — validate tag contract across a vault

_tags_app = typer.Typer(
    name="tags", help="Manage and validate memory tags.", no_args_is_help=True
)
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
    import os

    import uvicorn

    settings = load_settings(config)
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

    asyncio.run(mcp_main())


# ── migrate (M13) ──────────────────────────────────────────────────────────────
# Subcommand tree:
#   mnemos migrate from-ai-brain   — migrate ai-brain data to Mnemos format

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
    config: str = ConfigOption,
) -> None:
    """Mint a new bearer token and print it ONCE."""
    store = _auth_store(config)
    try:
        token_id, plaintext = store.create_token(name=name, expires_at=expires)
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
    table = Table("token_id", "name", "created_at", "expires_at", "disabled_at")
    for t in tokens:
        table.add_row(
            str(t.get("token_id", "")),
            str(t.get("name") or ""),
            str(t.get("created_at", "")),
            str(t.get("expires_at") or ""),
            str(t.get("disabled_at") or ""),
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
