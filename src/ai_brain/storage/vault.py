"""Obsidian vault integration — read/write markdown files with YAML frontmatter."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import frontmatter

from ai_brain.models import Memory, MemorySource, MemoryType


class VaultManager:
    """Manages the Obsidian-compatible markdown vault."""

    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path
        self.vault_path.mkdir(parents=True, exist_ok=True)

    def _sanitize_filename(self, title: str) -> str:
        """Create safe filename from title."""
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
        safe = safe.strip()[:80]
        return safe or "untitled"

    def _memory_dir(self, memory: Memory) -> Path:
        """Get subdirectory based on memory type."""
        return self.vault_path / memory.memory_type.value

    def memory_to_file(self, memory: Memory) -> Path:
        """Write memory as a markdown file with YAML frontmatter. Returns file path."""
        target_dir = self._memory_dir(memory)
        target_dir.mkdir(parents=True, exist_ok=True)

        filename = self._sanitize_filename(memory.auto_title())
        file_path = target_dir / f"{filename}.md"

        # Avoid overwriting unrelated files
        if file_path.exists() and memory.file_path and str(file_path) != memory.file_path:
            file_path = target_dir / f"{filename}_{memory.id[:8]}.md"

        post = frontmatter.Post(memory.content)
        post.metadata["id"] = memory.id
        post.metadata["title"] = memory.auto_title()
        post.metadata["tags"] = memory.tags
        post.metadata["source"] = memory.source.value
        if memory.source_url:
            post.metadata["source_url"] = memory.source_url
        post.metadata["memory_type"] = memory.memory_type.value
        post.metadata["created"] = memory.created_at.isoformat()
        post.metadata["updated"] = memory.updated_at.isoformat()
        if memory.metadata:
            post.metadata["extra"] = memory.metadata

        file_path.write_text(frontmatter.dumps(post), encoding="utf-8")
        return file_path

    def file_to_memory(self, file_path: Path) -> Memory | None:
        """Read a markdown file and parse into Memory object."""
        if not file_path.is_file() or file_path.suffix != ".md":
            return None

        try:
            post = frontmatter.load(str(file_path))
        except Exception:
            return None

        meta = post.metadata
        content = post.content

        if not content.strip():
            return None

        return Memory(
            id=meta.get("id", str(file_path)),
            content=content,
            title=meta.get("title"),
            tags=meta.get("tags", []),
            source=MemorySource(meta.get("source", "obsidian")),
            source_url=meta.get("source_url"),
            memory_type=MemoryType(meta.get("memory_type", "note")),
            created_at=_parse_dt(meta.get("created")),
            updated_at=_parse_dt(meta.get("updated")),
            metadata=meta.get("extra", {}),
            file_path=str(file_path),
        )

    def scan_vault(self) -> list[Memory]:
        """Scan the entire vault and return all parseable memories."""
        memories = []
        for md_file in self.vault_path.rglob("*.md"):
            memory = self.file_to_memory(md_file)
            if memory:
                memories.append(memory)
        return memories

    def delete_file(self, file_path: str) -> bool:
        """Delete a markdown file from the vault."""
        p = Path(file_path)
        if p.is_file() and p.suffix == ".md" and self.vault_path in p.parents:
            p.unlink()
            return True
        return False


def _parse_dt(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now(timezone.utc)
