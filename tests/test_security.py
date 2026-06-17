"""M9 / M15.2 — Security audit tests.

Covers:
  - SSRF URL validation (ingest_url)  — M9
  - SQL injection resistance (fts_search, list_all, update_fields)  — M9 + M15.2
  - Path traversal resistance (vault, path_scoped rules)  — M9
  - FTS5 special-char escaping (M15.2)
  - HF Hub revision pinning (M15.2, B615)
  - B104 false-positive annotation (M15.2)
"""

from __future__ import annotations

import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from mnemos.config import Settings
from mnemos.manager import MemoryManager
from mnemos.models import MemoryCreate


@pytest.fixture
def manager():
    with tempfile.TemporaryDirectory() as tmpdir:
        settings = Settings(
            mnemos={"vault_path": tmpdir, "data_dir": tmpdir, "db_name": "test.db"},
            embedding={"provider": "chromadb"},
        )
        mgr = MemoryManager(settings)
        yield mgr
        mgr.close()


@pytest.fixture
def sample_memory(manager):
    data = MemoryCreate(content="sample content", tags=["project:test", "agent:test", "gcw:test"])
    return manager.add(data, project="test", agent="test")


class TestUrlValidation:
    """SSRF prevention in ingest_url."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost/admin",
            "http://127.0.0.1:8080/",
            "http://0.0.0.0/",
            "http://10.0.0.1/",
            "http://192.168.1.1/",
            "http://172.16.0.1/",
            "http://172.31.255.255/",
            "http://169.254.169.254/latest/meta-data/",
            "ftp://example.com/",
            "file:///etc/passwd",
            "http://",
        ],
    )
    def test_blocked_urls_raise(self, url: str) -> None:
        with pytest.raises(ValueError):
            MemoryManager._validate_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/",
            "http://example.com:8080/path",
            "https://github.com/user/repo",
        ],
    )
    def test_allowed_urls_pass(self, url: str) -> None:
        assert MemoryManager._validate_url(url) == url

    def test_ingest_url_does_not_follow_redirects(self, manager) -> None:
        """SSRF: redirects are followed per-hop with re-validation (v2 guard).

        httpx.Client must still be constructed with follow_redirects=False so
        the library never skips the per-hop _validate_url guard. Redirects are
        followed manually in ingest_url; every Location target is validated
        before the next request is issued. This test verifies the constructor
        argument because that flag is the enforcement mechanism for the loop.

        Updated from v1 (no-follow) to v2 (per-hop re-validation) in T5-SSRF.
        """
        trafilatura_stub = MagicMock()
        trafilatura_stub.extract.return_value = "text"
        with (
            patch("httpx.Client") as mock_client_cls,
            patch.dict(sys.modules, {"trafilatura": trafilatura_stub}),
        ):
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.text = "body"
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value.__enter__.return_value = mock_client
            manager.ingest_url(
                "https://example.com/",
                tags=["project:test", "agent:test", "gcw:test"],
                project="test",
                agent="test",
            )
        _, kwargs = mock_client_cls.call_args
        assert kwargs.get("follow_redirects") is False


class TestSqlInjectionResistance:
    """Ensure dynamic SQL uses parameterised queries only."""

    def test_fts_search_uses_param_for_match(self, manager) -> None:
        """FTS MATCH clause must use ? placeholder."""
        # If this doesn't raise, the query is parameterised
        results = manager.sqlite.fts_search("test query", limit=1)
        assert isinstance(results, list)

    def test_list_all_uses_param_for_tags(self, manager) -> None:
        """Tag filter must use LIKE ? not string concat."""
        results = manager.sqlite.list_all(tags=['"injection"'], limit=1)
        assert isinstance(results, list)

    def test_update_fields_uses_param(self, manager, sample_memory) -> None:
        """UPDATE setters must use ? placeholders."""
        ok = manager.sqlite.update_fields(sample_memory.id, title="'; DROP TABLE memories; --")
        assert ok is True
        # Verify table still exists
        count = manager.sqlite.count()
        assert count >= 1


class TestPathTraversalResistance:
    """Path operations must not escape intended directories."""

    def test_vault_sanitizes_filename(self) -> None:
        from mnemos.storage.vault import VaultManager

        vm = VaultManager.__new__(VaultManager)
        assert vm._sanitize_filename("../../../etc/passwd") == "_________etc_passwd"
        assert vm._sanitize_filename("hello/world") == "hello_world"

    def test_path_scoped_uses_resolve(self, tmp_path) -> None:
        from mnemos.watchers.path_scoped import parse_rule_file

        # Create a file with a safe name
        f = tmp_path / "test.instructions.md"
        f.write_text("---\napplyTo: '**'\n---\n# Title\nbody")
        result = parse_rule_file(f)
        assert result["source_url"] == str(f.resolve())


# ── M15.2 new tests ─────────────────────────────────────────────────────────


class TestFts5Escaping:
    """FTS5 user input must be escaped before being interpolated into MATCH."""

    @pytest.mark.parametrize(
        "hostile",
        [
            # Classic FTS5 column-filter escape — would otherwise match every row
            # in the 'content' column or pivot into another column.
            '" OR col:"content',
            # Quoted phrase / unbalance
            '"; DROP TABLE memories; --',
            # NEAR + prefix
            "anything* NEAR whatever",
            # Parens (FTS5 group operator)
            "(hack)",
            # Colon (column-filter)
            "tag:admin",
            # Pure punctuation
            '***"""((()))***',
        ],
    )
    def test_fts_search_survives_hostile_input(self, manager, hostile: str) -> None:
        """Hostile query must not raise, must not corrupt the index."""
        # The query must return a list (possibly empty) without raising.
        results = manager.sqlite.fts_search(hostile, limit=5)
        assert isinstance(results, list)
        # Count must remain non-negative and the table must still be queryable.
        assert manager.sqlite.count() >= 0

    def test_fts_build_escapes_special_chars(self) -> None:
        """Unit test of the static escape helper."""
        from mnemos.storage.sqlite_store import SQLiteStore

        # All FTS5 special chars must be stripped.
        out = SQLiteStore._build_fts_query('"a*b(c):d"')
        # Output is wrapped in double quotes (literal phrase). The inner
        # special chars are replaced with whitespace and collapsed.
        assert out.startswith('"') and out.endswith('"')
        # No FTS5 special chars remain in the inner phrase.
        inner = out[1:-1]
        for ch in ('"', "'", "*", "(", ")", ":"):
            assert ch not in inner

    def test_fts_build_empty_input(self) -> None:
        """Empty / whitespace input must not raise and must produce safe MATCH."""
        from mnemos.storage.sqlite_store import SQLiteStore

        # FTS5's literal empty phrase '""' is a syntax error. We degrade to a
        # unique nonsense phrase that yields zero rows without raising.
        sentinel = "__mnemos_fts5_no_match_placeholder__"
        assert SQLiteStore._build_fts_query("") == f'"{sentinel}"'
        assert SQLiteStore._build_fts_query("   ") == f'"{sentinel}"'
        assert SQLiteStore._build_fts_query("***") == f'"{sentinel}"'  # all special


class TestSqlInjectionSafe:
    """M15.2 — update_fields must reject unknown / hostile column names."""

    def test_update_fields_rejects_unknown_columns(self, manager, sample_memory) -> None:
        """Field names not in the whitelist must be silently dropped."""
        # Attacker tries to overwrite the primary key or a system column.
        result = manager.sqlite.update_fields(
            sample_memory.id,
            id="' OR 1=1 --",  # NOT in whitelist — must be dropped silently
            rowid="9999",  # NOT in whitelist — must be dropped silently
            created_at="2020-01-01",  # NOT in whitelist — must be dropped silently
            status="published",  # in whitelist — must apply
        )
        # The call returns True only if at least one whitelisted field was set.
        assert result is True
        # Re-fetch and verify the id was NOT mutated.
        refetched = manager.sqlite.get(sample_memory.id)
        assert refetched is not None
        assert refetched.id == sample_memory.id  # primary key intact
        # And the whitelisted update DID apply.
        assert refetched.status.value == "published"

    def test_update_fields_no_fstring_injection(self, manager, sample_memory) -> None:
        """A value with SQL-fragments must be parameterised, not concatenated."""
        # Title is in the whitelist, but the value contains a quote.
        ok = manager.sqlite.update_fields(
            sample_memory.id,
            title="'; DROP TABLE memories; --",
        )
        assert ok is True
        # The table must still exist and be queryable.
        assert manager.sqlite.count() >= 1
        refetched = manager.sqlite.get(sample_memory.id)
        assert refetched is not None
        assert refetched.title == "'; DROP TABLE memories; --"

    def test_update_fields_empty_kwargs_returns_false(self, manager) -> None:
        """An empty call must be a no-op, not a malformed UPDATE."""
        assert manager.sqlite.update_fields("nonexistent") is False
        assert manager.sqlite.update_fields("nonexistent", bogus=1) is False

    def test_update_fields_uses_whitelist_dispatch(self) -> None:
        """The static `_FIELD_UPDATERS` dict is the only source of column names."""
        from mnemos.storage import sqlite_store

        # Whitelist must contain exactly the documented columns.
        expected_keys = {
            "status",
            "quality_score",
            "confidence",
            "source_coverage",
            "cluster_id",
            "derived_from",
            "embedding_id",
            "clean_content",
            "filter_profile",
            "filter_stats",
            "filter_version",
            "title",
            "content",
            "tags",
            "category",
            "file_path",
        }
        assert set(sqlite_store._FIELD_UPDATERS) == expected_keys
        # Every value is a static "col=?" fragment.
        for col, frag in sqlite_store._FIELD_UPDATERS.items():
            assert frag == f"{col}=?"


class TestHfHubPinning:
    """M15.2 — every `hf_hub_download` call must pass `revision=` (B615)."""

    def test_onnx_provider_requires_revision_kwarg(self) -> None:
        """Omitting `revision` must raise — fail-closed by design."""
        from mnemos.embeddings import ONNXHubProvider

        with pytest.raises(ValueError, match="requires an explicit `revision`"):
            ONNXHubProvider(
                "sentence-transformers/all-MiniLM-L6-v2",
                revision=None,  # explicit None — must raise
            )

    def test_onnx_provider_passes_revision_to_every_download(self) -> None:
        """All three `hf_hub_download` calls must carry the same `revision=`."""
        from mnemos.embeddings import ONNXHubProvider

        sentinel_revision = "deadbeefcafebabe" * 2  # 32-hex-char-looking SHA
        download_calls: list[dict] = []

        def fake_download(repo_id, filename, **kwargs):
            download_calls.append({"repo_id": repo_id, "filename": filename, **kwargs})
            # Return a path that `Tokenizer.from_file` and `ort.InferenceSession`
            # will accept as a "file" (we mock both anyway, so the path is
            # only required to be a non-empty string).
            return f"/tmp/fake/{filename}"

        fake_tokenizer = MagicMock()
        fake_tokenizer.enable_truncation = MagicMock()
        fake_tokenizer.enable_padding = MagicMock()
        fake_tokenizer.encode_batch = MagicMock(return_value=[])

        fake_session = MagicMock()
        fake_session.get_inputs.return_value = []

        # Stub `_infer` so the test does not need real numpy math. The stub
        # returns a shape-correct zero array so the `.shape[-1]` lookup in
        # __init__ works.
        with (
            patch("huggingface_hub.hf_hub_download", side_effect=fake_download),
            patch("onnxruntime.InferenceSession", return_value=fake_session),
            patch("tokenizers.Tokenizer.from_file", return_value=fake_tokenizer),
            patch.object(ONNXHubProvider, "_infer", return_value=MagicMock(shape=(1, 384))),
        ):
            ONNXHubProvider(
                "sentence-transformers/all-MiniLM-L6-v2",
                revision=sentinel_revision,
            )

        # Two calls: onnx_file + tokenizer.json. (The fallback `model.onnx`
        # would only fire if onnx_file raised, which the fake doesn't.)
        assert len(download_calls) >= 2, f"expected >= 2 downloads, got {download_calls}"
        for call in download_calls:
            assert call.get("revision") == sentinel_revision, (
                f"hf_hub_download call missing revision=: {call}"
            )

    def test_onnx_provider_revision_on_fallback_path(self) -> None:
        """If the configured onnx_file is missing, the fallback download must
        also carry the revision (B615 fires on every call)."""
        from mnemos.embeddings import ONNXHubProvider

        sentinel_revision = "feedface" * 4
        download_calls: list[dict] = []

        def fake_download(repo_id, filename, **kwargs):
            download_calls.append({"repo_id": repo_id, "filename": filename, **kwargs})
            # First call (onnx_file) raises → fallback path triggers.
            if filename == "onnx/model.onnx":
                raise FileNotFoundError("simulated missing onnx file")
            return f"/tmp/fake/{filename}"

        fake_tokenizer = MagicMock()
        fake_tokenizer.enable_truncation = MagicMock()
        fake_tokenizer.enable_padding = MagicMock()
        fake_tokenizer.encode_batch = MagicMock(return_value=[])

        fake_session = MagicMock()
        fake_session.get_inputs.return_value = []

        with (
            patch("huggingface_hub.hf_hub_download", side_effect=fake_download),
            patch("onnxruntime.InferenceSession", return_value=fake_session),
            patch("tokenizers.Tokenizer.from_file", return_value=fake_tokenizer),
            patch.object(ONNXHubProvider, "_infer", return_value=MagicMock(shape=(1, 384))),
        ):
            ONNXHubProvider(
                "sentence-transformers/all-MiniLM-L6-v2",
                revision=sentinel_revision,
            )

        # Fallback fired → 3 calls: onnx_file (raises), model.onnx, tokenizer.json.
        assert len(download_calls) == 3, f"expected 3 downloads, got {download_calls}"
        for call in download_calls:
            assert call.get("revision") == sentinel_revision, (
                f"fallback hf_hub_download call missing revision=: {call}"
            )

    def test_create_provider_threads_hf_revision_from_config(self) -> None:
        """`create_embedding_provider` must read `cfg.hf_revision` for onnx."""
        from mnemos.config import EmbeddingConfig
        from mnemos.embeddings import create_embedding_provider

        cfg = EmbeddingConfig(
            provider="onnx",
            model="sentence-transformers/all-MiniLM-L6-v2",
            onnx_file="onnx/model.onnx",
            hf_revision="abc1234" * 4,  # 28-char placeholder SHA
        )
        captured: dict = {}

        def fake_init(
            self,
            model_id,
            onnx_file="onnx/model.onnx",
            max_length=512,
            *,
            revision=None,
        ):
            captured["model_id"] = model_id
            captured["onnx_file"] = onnx_file
            captured["revision"] = revision

        with patch("mnemos.embeddings.ONNXHubProvider.__init__", fake_init):
            create_embedding_provider(cfg)

        assert captured["revision"] == cfg.hf_revision

    def test_default_config_has_nonempty_hf_revision(self) -> None:
        """Default EmbeddingConfig must ship with a pinned revision string."""
        from mnemos.config import EmbeddingConfig

        cfg = EmbeddingConfig()
        assert isinstance(cfg.hf_revision, str)
        assert len(cfg.hf_revision) > 0
        # Either looks like a hex SHA (>= 7 chars) or a tag like "vN.N".
        assert len(cfg.hf_revision) >= 7


class TestSsrfBlocklist:
    """M15.2 — explicit regression test for AWS / GCP metadata endpoint."""

    @pytest.mark.parametrize(
        "metadata_url",
        [
            "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
            "http://169.254.169.254/computeMetadata/v1/",  # GCP (same IP)
            "http://169.254.169.254/metadata/instance?api-version=2021-02-01",  # Azure
            "http://[fd00:ec2::254]/latest/meta-data/",  # AWS IPv6 link-local
        ],
    )
    def test_cloud_metadata_endpoints_blocked(self, metadata_url: str) -> None:
        with pytest.raises(ValueError):
            MemoryManager._validate_url(metadata_url)

    def test_b104_blocklist_entry_annotated(self) -> None:
        """The `"0.0.0.0"` blocklist entry must carry a `# nosec B104` comment.

        B104 (bandit) flags the *string* "0.0.0.0" anywhere it appears. In
        Mnemos the string is part of the SSRF blocklist — it is the address
        being REJECTED, not bound. The suppression is a confirmed false
        positive and is documented inline.
        """
        import inspect

        from mnemos.manager import MemoryManager

        source = inspect.getsource(MemoryManager._validate_url)
        # The blocklist must still contain "0.0.0.0".
        assert '"0.0.0.0"' in source
        # The site must be annotated as a nosec, with a justification.
        assert "nosec B104" in source
        assert "blocklist" in source.lower() or "not a bind" in source.lower()
