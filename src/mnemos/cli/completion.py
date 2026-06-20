"""``mnemos completion`` CLI subcommand — shell completion auto-install.

Generates a shell completion script for bash/zsh/fish and auto-installs it
into the right rc file. Idempotent: re-running does not duplicate the source
line.

Subcommand tree::

    mnemos completion                     — auto-detect shell + auto-install
    mnemos completion bash|zsh|fish       — explicit shell + auto-install
    mnemos completion --show-instructions — print manual steps, no file changes
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from typer.completion import (  # type: ignore[attr-defined]  # typer stubs don't export this, but it exists at runtime
    get_completion_script,
)

console = Console()

completion_app = typer.Typer(
    name="completion",
    help="Install shell completion for mnemos (auto-detect + auto-install).",
    no_args_is_help=False,
)

# Env var Click/Typer uses to dispatch completion requests at runtime.
_COMPLETE_VAR = "_MNEMOS_COMPLETE"
_PROG_NAME = "mnemos"

# Shells we support for auto-install.
_SUPPORTED_SHELLS = ("bash", "zsh", "fish")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _detect_shell() -> str | None:
    """Detect the current shell from ``$SHELL``.

    Returns the bare shell name (``bash``/``zsh``/``fish``) or ``None`` if
    unknown/unsupported.
    """
    raw = os.environ.get("SHELL", "")
    if not raw:
        return None
    name = raw.split("/")[-1].lower()
    if name in _SUPPORTED_SHELLS:
        return name
    return None


def _rc_path(shell: str) -> Path:
    """Return the rc file path for the given shell."""
    home = Path.home()
    if shell == "bash":
        return home / ".bashrc"
    if shell == "zsh":
        return home / ".zshrc"
    if shell == "fish":
        return home / ".config" / "fish" / "completions" / f"{_PROG_NAME}.fish"
    raise ValueError(f"Unsupported shell: {shell}")


def _source_line(shell: str) -> str:
    """Build the source line to append to the rc file for the given shell.

    For bash/zsh we use ``eval "$(mnemos ...)"`` which is the idiomatic
    Click/Typer pattern. For fish we write the completion script directly
    into the fish completions directory (fish sources it automatically).
    """
    if shell == "fish":
        # fish: the rc file IS the completion script, no source line needed.
        return ""
    return f'eval "$({_PROG_NAME} --show-completion {shell})"'  # nosec B604 — shell completion string for rc file, not Python eval()


def _completion_script(shell: str) -> str:
    """Generate the completion script for the given shell."""
    return get_completion_script(
        prog_name=_PROG_NAME,
        complete_var=_COMPLETE_VAR,
        shell=shell,  # nosec B604 — shell is a completion type string (bash/zsh/fish), not shell=True
    )


def _is_installed(shell: str, rc: Path) -> bool:
    """Check whether the completion source line is already in the rc file."""
    if shell == "fish":
        return rc.exists()
    if not rc.exists():
        return False
    marker = f"{_PROG_NAME} --show-completion {shell}"
    try:
        return marker in rc.read_text(encoding="utf-8")
    except OSError:
        return False


def _install(shell: str) -> bool:
    """Install completion for the given shell.

    Returns True if installed (or already installed), False on write error.
    """
    rc = _rc_path(shell)
    if _is_installed(shell, rc):
        console.print(f"[green]✓[/green] Completion for {shell} already installed at {rc}")
        return True

    if shell == "fish":
        # fish: write the completion script directly into the completions dir.
        try:
            rc.parent.mkdir(parents=True, exist_ok=True)
            rc.write_text(_completion_script(shell), encoding="utf-8")
        except OSError as exc:
            console.print(f"[red]✗ Failed to write {rc}: {exc}[/red]")
            return False
        console.print(f"[green]✓[/green] Installed {shell} completion → {rc}")
        return True

    # bash/zsh: append the source line to the rc file.
    line = _source_line(shell)
    try:
        rc.parent.mkdir(parents=True, exist_ok=True)
        with rc.open("a", encoding="utf-8") as fh:
            fh.write(f"\n# Added by `mnemos completion` ({shell})\n{line}\n")
    except OSError as exc:
        console.print(f"[red]✗ Failed to write {rc}: {exc}[/red]")
        return False
    console.print(f"[green]✓[/green] Installed {shell} completion → {rc}")
    console.print(f"  [dim]Restart your shell or run: source {rc}[/dim]")
    return True


# ── Manual instructions ────────────────────────────────────────────────────────


def _print_instructions() -> None:
    """Print manual install instructions for all supported shells."""
    console.print("[bold]Manual completion installation[/bold]\n")
    for shell in _SUPPORTED_SHELLS:
        rc = _rc_path(shell)
        line = _source_line(shell) or f"# fish: completion script written to {rc}"
        console.print(f"[bold cyan]{shell}[/bold cyan]  →  {rc}")
        console.print(f"    {line}")
        console.print()


# ── Command ───────────────────────────────────────────────────────────────────


@completion_app.callback(invoke_without_command=True)
def completion(
    shell: Annotated[
        str | None,
        typer.Argument(
            help="Shell to install completion for (bash/zsh/fish). "
            "If omitted, auto-detects from $SHELL.",
        ),
    ] = None,
    show_instructions: Annotated[
        bool,
        typer.Option(
            "--show-instructions",
            help="Print manual install instructions without modifying any files.",
        ),
    ] = False,
) -> None:
    """Install shell completion for mnemos.

    With no arguments: auto-detects the current shell from ``$SHELL`` and
    auto-installs the completion script into the right rc file. Idempotent —
    re-running won't duplicate the source line.

    Pass an explicit shell (bash/zsh/fish) to override auto-detection.

    Use ``--show-instructions`` to print the manual steps for all supported
    shells without modifying any files.
    """
    if show_instructions:
        _print_instructions()
        raise typer.Exit(0)

    target = shell or _detect_shell()
    if target is None:
        console.print(
            "[red]Could not auto-detect your shell from $SHELL.[/red]\n"
            "Pass an explicit shell: [bold]mnemos completion bash|zsh|fish[/bold]\n"
            "Or see manual steps: [bold]mnemos completion --show-instructions[/bold]"
        )
        raise typer.Exit(1)

    if target not in _SUPPORTED_SHELLS:
        console.print(
            f"[red]Unsupported shell: {target!r}.[/red]\nSupported: {', '.join(_SUPPORTED_SHELLS)}"
        )
        raise typer.Exit(1)

    ok = _install(target)
    if not ok:
        raise typer.Exit(1)
