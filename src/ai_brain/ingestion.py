"""Ingestion pipeline — parse and import data from various sources."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from ai_brain.models import MemoryCreate, MemorySource, MemoryType

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """Parse content from various sources into MemoryCreate objects."""

    def from_text(
        self,
        text: str,
        title: str | None = None,
        tags: list[str] | None = None,
        source: MemorySource = MemorySource.MANUAL,
    ) -> MemoryCreate:
        """Simple text → memory."""
        return MemoryCreate(
            content=text.strip(),
            title=title,
            tags=tags or [],
            source=source,
            memory_type=MemoryType.NOTE,
        )

    def from_url(self, url: str, tags: list[str] | None = None) -> MemoryCreate:
        """Fetch and parse a web page → memory."""
        import trafilatura

        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            raise ValueError(f"Failed to fetch URL: {url}")

        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        if not text:
            raise ValueError(f"Failed to extract content from: {url}")

        title = trafilatura.extract(downloaded, output_format="xml")
        # Simpler: use first line as title
        first_line = text.strip().split("\n")[0][:100]

        return MemoryCreate(
            content=text,
            title=first_line,
            tags=tags or ["web"],
            source=MemorySource.WEB,
            source_url=url,
            memory_type=MemoryType.BOOKMARK,
        )

    def from_file(self, file_path: Path, tags: list[str] | None = None) -> MemoryCreate:
        """Parse a file (txt, md, pdf, docx) → memory."""
        suffix = file_path.suffix.lower()

        if suffix in (".txt", ".md"):
            content = file_path.read_text(encoding="utf-8")
        elif suffix == ".pdf":
            content = self._extract_pdf(file_path)
        elif suffix == ".docx":
            content = self._extract_docx(file_path)
        else:
            raise ValueError(f"Unsupported file type: {suffix}")

        return MemoryCreate(
            content=content,
            title=file_path.stem,
            tags=tags or [f"file:{suffix.lstrip('.')}"],
            source=MemorySource.FILE,
            memory_type=MemoryType.NOTE,
            metadata={"original_file": str(file_path)},
        )

    def chunk_text(self, text: str, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
        """Split long text into overlapping chunks."""
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            # Try to break at paragraph or sentence boundary
            if end < len(text):
                for sep in ["\n\n", "\n", ". ", " "]:
                    last_sep = text[start:end].rfind(sep)
                    if last_sep > chunk_size // 2:
                        end = start + last_sep + len(sep)
                        break

            chunks.append(text[start:end].strip())
            start = end - overlap

        return [c for c in chunks if c]

    def content_hash(self, text: str) -> str:
        """Generate hash for deduplication."""
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def _extract_pdf(self, file_path: Path) -> str:
        try:
            import pymupdf

            doc = pymupdf.open(str(file_path))
            text = "\n\n".join(page.get_text() for page in doc)
            doc.close()
            return text
        except ImportError:
            raise ImportError("Install pymupdf: pip install 'ai-brain[pdf]'")

    def _extract_docx(self, file_path: Path) -> str:
        try:
            import docx

            doc = docx.Document(str(file_path))
            return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except ImportError:
            raise ImportError("Install python-docx: pip install 'ai-brain[docx]'")
