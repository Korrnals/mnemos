"""Tests for BrainWatcher — file scanning, dedup, ignore rules."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai_brain.config import BrainConfig, EmbeddingConfig, Settings
from ai_brain.manager import MemoryManager
from ai_brain.watcher import BrainWatcher


@pytest.fixture(scope="module")
def manager(tmp_path_factory: pytest.TempPathFactory) -> MemoryManager:
    tmp = tmp_path_factory.mktemp("brain")
    settings = Settings(
        brain=BrainConfig(
            vault_path=tmp / "vault",
            data_dir=tmp / "data",
        ),
        embedding=EmbeddingConfig(provider="chromadb"),
    )
    settings.resolve_paths()
    mgr = MemoryManager(settings)
    # Fake embedder — watcher tests verify scan logic, not embeddings
    fake = MagicMock()
    fake.embed.return_value = [0.0] * 384
    mgr._embedder = fake
    yield mgr
    mgr.close()


@pytest.fixture
def watcher(manager: MemoryManager) -> BrainWatcher:
    return BrainWatcher(manager=manager)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a fake project with various files."""
    proj = tmp_path / "my_project"
    proj.mkdir()
    (proj / "pyproject.toml").write_text('[project]\nname = "test"')
    (proj / "main.py").write_text("def main():\n    print('hello')\n")
    (proj / "notes.md").write_text("# Notes\n\nSome important notes.\n")
    (proj / "config.yaml").write_text("key: value\n")

    # Should be ignored
    (proj / ".git").mkdir()
    (proj / ".git" / "HEAD").write_text("ref: refs/heads/main")
    (proj / "__pycache__").mkdir()
    (proj / "__pycache__" / "main.cpython-314.pyc").write_text("bytecode")
    (proj / "node_modules").mkdir()
    (proj / "node_modules" / "pkg.js").write_text("module.exports = {}")

    return proj


def test_scan_indexes_files(watcher: BrainWatcher, project_dir: Path):
    count = watcher.scan_directory(project_dir)
    assert count == 4  # pyproject.toml, main.py, notes.md, config.yaml
    assert watcher.stats["ingested"] == 4


def test_scan_ignores_dirs(watcher: BrainWatcher, project_dir: Path):
    watcher.scan_directory(project_dir)
    assert watcher.stats["skipped"] >= 3  # .git/HEAD, __pycache__/*, node_modules/*


def test_scan_dedup(watcher: BrainWatcher, project_dir: Path):
    watcher.scan_directory(project_dir)
    first_ingested = watcher.stats["ingested"]

    count2 = watcher.scan_directory(project_dir)
    assert count2 == 0  # all already indexed
    assert watcher.stats["ingested"] == first_ingested  # no new ingestions


def test_scan_updates_on_change(watcher: BrainWatcher, project_dir: Path):
    watcher.scan_directory(project_dir)
    assert watcher.stats["updated"] == 0

    # Modify a file
    (project_dir / "main.py").write_text("def main():\n    print('updated!')\n")
    watcher.scan_directory(project_dir)
    assert watcher.stats["updated"] == 1


def test_ignores_large_files(watcher: BrainWatcher, project_dir: Path):
    big = project_dir / "big.txt"
    big.write_text("x" * (600 * 1024))  # > 512KB default

    watcher.scan_directory(project_dir)
    # big.txt should be skipped, not in the 4 indexed
    assert watcher.stats["ingested"] == 4


def test_ignores_empty_files(watcher: BrainWatcher, project_dir: Path):
    (project_dir / "empty.py").write_text("")
    watcher.scan_directory(project_dir)
    assert watcher.stats["ingested"] == 4  # empty.py not counted


def test_ignores_unknown_extensions(watcher: BrainWatcher, project_dir: Path):
    (project_dir / "data.bin").write_bytes(b"\x00\x01\x02")
    watcher.scan_directory(project_dir)
    assert watcher.stats["ingested"] == 4  # .bin not in extensions


def test_detect_project(project_dir: Path):
    name = BrainWatcher._detect_project(project_dir / "main.py")
    assert name == "my_project"
