"""Background file watcher — autonomously indexes workspace files into brain memory."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from ai_brain.manager import MemoryManager
from ai_brain.models import MemoryCreate, MemorySource, MemoryType, MemoryUpdate

logger = logging.getLogger(__name__)

DEFAULT_IGNORE_DIRS = {
    ".git", ".svn", ".hg", "__pycache__", ".mypy_cache", ".pytest_cache",
    "node_modules", ".venv", "venv", ".env", ".tox", ".eggs",
    "dist", "build", ".ruff_cache", ".next", ".nuxt",
    ".ai-brain", "chroma_data", ".cache", ".local",
}

DEFAULT_EXTENSIONS = {
    ".md", ".txt", ".rst",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".vue", ".svelte",
    ".yaml", ".yml", ".toml", ".json", ".ini", ".cfg",
    ".sh", ".bash", ".zsh", ".fish",
    ".css", ".scss", ".html", ".xml",
    ".sql", ".graphql",
    ".go", ".rs", ".rb", ".java", ".kt", ".c", ".cpp", ".h",
    ".dockerfile", ".containerfile",
}

MAX_FILE_SIZE = 512 * 1024  # 512 KB


class _ChangeHandler(FileSystemEventHandler):
    """Watchdog event handler — forwards file changes to BrainWatcher."""

    def __init__(self, watcher: BrainWatcher) -> None:
        self._watcher = watcher
        self._debounce: dict[str, float] = {}

    def on_created(self, event) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._handle(event.src_path)

    def on_modified(self, event) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._handle(event.src_path)

    def _handle(self, src_path: str) -> None:
        now = time.monotonic()
        if src_path in self._debounce and now - self._debounce[src_path] < 2.0:
            return
        self._debounce[src_path] = now
        # Purge stale entries periodically
        if len(self._debounce) > 5000:
            cutoff = now - 10.0
            self._debounce = {k: v for k, v in self._debounce.items() if v > cutoff}
        try:
            self._watcher.ingest_file(Path(src_path))
        except Exception:
            logger.exception("Failed to ingest %s", src_path)


class BrainWatcher:
    """Autonomous file watcher that indexes workspaces into brain memory."""

    def __init__(
        self,
        manager: MemoryManager,
        ignore_dirs: set[str] | None = None,
        extensions: set[str] | None = None,
        max_file_size: int = MAX_FILE_SIZE,
    ) -> None:
        self.manager = manager
        self.ignore_dirs = ignore_dirs or DEFAULT_IGNORE_DIRS
        self.extensions = extensions or DEFAULT_EXTENSIONS
        self.max_file_size = max_file_size
        self._observers: list[Observer] = []
        self._stats = {"ingested": 0, "updated": 0, "skipped": 0, "errors": 0}

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    # ── Filtering ─────────────────────────────────────────────────────────

    def _should_ignore(self, path: Path) -> bool:
        for part in path.parts:
            if part in self.ignore_dirs or part.endswith(".egg-info"):
                return True
        if path.suffix.lower() not in self.extensions:
            return True
        try:
            size = path.stat().st_size
            if size > self.max_file_size or size == 0:
                return True
        except OSError:
            return True
        return False

    @staticmethod
    def _detect_project(path: Path) -> str:
        """Walk up to find a project root (directory with a VCS/build marker)."""
        markers = {
            ".git", "pyproject.toml", "package.json", "Cargo.toml",
            "go.mod", "Makefile", "CMakeLists.txt", "pom.xml",
        }
        current = path.parent
        for _ in range(20):
            if any((current / m).exists() for m in markers):
                return current.name
            parent = current.parent
            if parent == current:
                break
            current = parent
        return path.parent.name

    @staticmethod
    def _content_hash(content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    # ── Ingestion ─────────────────────────────────────────────────────────

    def ingest_file(self, path: Path) -> None:
        """Ingest a single file into brain memory (create or update)."""
        path = path.resolve()
        if not path.is_file() or self._should_ignore(path):
            self._stats["skipped"] += 1
            return

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            self._stats["errors"] += 1
            return

        content = content.strip()
        if not content:
            self._stats["skipped"] += 1
            return

        project = self._detect_project(path)
        ext = path.suffix.lstrip(".")
        tags = [f"project:{project}", f"ext:{ext}", "auto-watch"]
        memory_type = MemoryType.NOTE if ext in ("md", "txt", "rst") else MemoryType.SNIPPET
        new_hash = self._content_hash(content)
        meta = {"file_path": str(path), "project": project, "content_hash": new_hash}

        existing = self.manager.sqlite.get_by_source_file(str(path))
        if existing:
            old_hash = (existing.metadata or {}).get("content_hash", "")
            if old_hash == new_hash:
                self._stats["skipped"] += 1
                return
            self.manager.update(
                existing.id,
                MemoryUpdate(content=content, tags=tags, metadata=meta),
            )
            self._stats["updated"] += 1
            logger.debug("Updated: %s", path.name)
        else:
            data = MemoryCreate(
                content=content,
                title=f"{path.name} ({project})",
                tags=tags,
                source=MemorySource.FILE,
                memory_type=memory_type,
                metadata=meta,
            )
            self.manager.add(data)
            self._stats["ingested"] += 1
            logger.debug("Ingested: %s", path.name)

    # ── Directory scan ────────────────────────────────────────────────────

    def scan_directory(self, root: Path) -> int:
        """Walk a directory tree and ingest all supported files. Returns count."""
        root = root.resolve()
        count = 0
        for path in root.rglob("*"):
            if path.is_file():
                before = self._stats["ingested"] + self._stats["updated"]
                self.ingest_file(path)
                if self._stats["ingested"] + self._stats["updated"] > before:
                    count += 1
        return count

    # ── Watch loop ────────────────────────────────────────────────────────

    def watch(self, paths: list[Path]) -> None:
        """Start watchdog observers on the given directories."""
        handler = _ChangeHandler(self)
        for p in paths:
            p = Path(p).expanduser().resolve()
            if not p.is_dir():
                logger.warning("Not a directory, skipping: %s", p)
                continue
            observer = Observer()
            observer.schedule(handler, str(p), recursive=True)
            observer.daemon = True
            observer.start()
            self._observers.append(observer)
            logger.info("Watching: %s", p)

    def stop(self) -> None:
        for obs in self._observers:
            obs.stop()
        for obs in self._observers:
            obs.join(timeout=5)
        self._observers.clear()

    def run_forever(self, paths: list[Path], initial_scan: bool = True) -> None:
        """Main entry point: scan + watch + block until interrupted."""
        resolved = []
        for p in paths:
            rp = Path(p).expanduser().resolve()
            if rp.is_dir():
                resolved.append(rp)

        if initial_scan:
            for rp in resolved:
                logger.info("Initial scan: %s", rp)
                count = self.scan_directory(rp)
                logger.info("Scanned %s: %d files indexed", rp.name, count)

        self.watch(resolved)
        logger.info("Watcher running. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
            logger.info("Watcher stopped. Stats: %s", self._stats)
