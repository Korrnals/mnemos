"""Round-3 (NM-1b+) unit tests: MRL heads, teacher instruct-template,
Qwen3 pooling, --from-mnemos-db collector, export mrl_dims detection.

Deliberately torch-free: tensor math is exercised through a numpy-backed
fake torch (the functions only need normalize/mean/sum/arange semantics),
the SQLite collector runs against a real temporary database. Heavy ML
imports are never required — the tests always run.
"""

from __future__ import annotations

import sqlite3
import sys
import types
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from training.dataset.prepare_dataset import collect_from_mnemos_db  # noqa: E402
from training.distill import (  # noqa: E402
    DEFAULT_EMBED_DIM,
    DEFAULT_TEACHER,
    aggregate_mrl_losses,
    detect_teacher_pooling,
    format_teacher_input,
    kd_cosine_loss,
    mrl_kd_loss,
    parse_mrl_dims,
    parse_mrl_weights,
)
from training.export_onnx import detect_mrl_dims  # noqa: E402

# ── MRL heads ────────────────────────────────────────────────────────────────


class TestParseMrlDims:
    def test_default_is_plain_single_dim(self) -> None:
        assert parse_mrl_dims("384", full_dim=384) == [384]

    def test_round3_quad_parse(self) -> None:
        assert parse_mrl_dims("64,128,256,384", full_dim=384) == [64, 128, 256, 384]

    @pytest.mark.parametrize(
        "bad", ["", "384,64", "128,128", "512", "0,64", "abc", "64,,128"]
    )
    def test_loud_failures(self, bad: str) -> None:
        with pytest.raises(ValueError, match="--mrl-dims"):
            parse_mrl_dims(bad, full_dim=384)


class TestMrlWeights:
    def test_uniform_default_sums_to_one(self) -> None:
        weights = parse_mrl_weights(None, 4)
        assert weights == [0.25] * 4
        assert sum(weights) == pytest.approx(1.0)

    def test_custom_weights_normalised(self) -> None:
        assert parse_mrl_weights("4,2,1,1", 4) == [0.5, 0.25, 0.125, 0.125]

    def test_count_mismatch_and_bad_values_fail_loud(self) -> None:
        with pytest.raises(ValueError, match="exactly 4"):
            parse_mrl_weights("1,2", 4)
        with pytest.raises(ValueError, match="non-negative"):
            parse_mrl_weights("1,-1,1,1", 4)
        with pytest.raises(ValueError, match="non-negative"):
            parse_mrl_weights("0,0,0,0", 4)


class TestMrlAggregation:
    def test_weighted_sum_over_floats(self) -> None:
        assert aggregate_mrl_losses([1.0, 2.0, 3.0, 4.0], [0.5, 0.25, 0.125, 0.125]) == (
            pytest.approx(1.875)
        )

    def test_length_mismatch_fails_loud(self) -> None:
        with pytest.raises(ValueError, match="mismatch"):
            aggregate_mrl_losses([1.0, 2.0], [1.0])


def _install_fake_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    def normalize(x: np.ndarray, p: int, dim: int) -> np.ndarray:
        norm = np.linalg.norm(x, ord=p, axis=dim, keepdims=True)
        return x / np.maximum(norm, 1e-12)

    fake = types.SimpleNamespace(
        nn=types.SimpleNamespace(functional=types.SimpleNamespace(normalize=normalize)),
        mean=np.mean,
        sum=lambda x, dim=-1: np.sum(x, axis=dim),
        arange=np.arange,
    )
    monkeypatch.setitem(sys.modules, "torch", fake)


class TestMrlKdLoss:
    """mrl_kd_loss numerics on numpy 'tensors' with torch faked out."""

    rng = np.random.default_rng(42)

    def _pair(self, n: int = 8, dim: int = 384) -> tuple[np.ndarray, np.ndarray]:
        student = self.rng.normal(size=(n, dim)).astype(np.float64)
        teacher = self.rng.normal(size=(n, dim)).astype(np.float64)
        return student, teacher

    def test_single_dim_mode_equals_plain_kd_loss(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_torch(monkeypatch)
        student, teacher = self._pair()
        plain = kd_cosine_loss(student, teacher, temperature=0.05)
        mrl = mrl_kd_loss(student, teacher, [384], 0.05)
        assert float(mrl) == pytest.approx(float(plain))

    def test_uniform_quad_is_mean_of_per_dim_losses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_torch(monkeypatch)
        student, teacher = self._pair()
        dims = [64, 128, 256, 384]
        per_dim = [
            float(kd_cosine_loss(student[..., :d], teacher[..., :d], 0.05)) for d in dims
        ]
        total = mrl_kd_loss(student, teacher, dims, 0.05)
        assert float(total) == pytest.approx(float(np.mean(per_dim)))

    def test_custom_weights_are_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_torch(monkeypatch)
        student, teacher = self._pair()
        dims = [64, 128, 256, 384]
        per_dim = [
            float(kd_cosine_loss(student[..., :d], teacher[..., :d], 0.05)) for d in dims
        ]
        total = mrl_kd_loss(student, teacher, dims, 0.05, weights=[4.0, 2.0, 1.0, 1.0])
        assert float(total) == pytest.approx(
            0.5 * per_dim[0] + 0.25 * per_dim[1] + 0.125 * per_dim[2] + 0.125 * per_dim[3]
        )

    def test_identical_vectors_have_zero_loss(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_torch(monkeypatch)
        vec = self.rng.normal(size=(4, 384))
        total = mrl_kd_loss(vec, vec.copy(), [64, 128, 256, 384], 0.05)
        assert float(total) == pytest.approx(0.0, abs=1e-9)


# ── Teacher instruct template + pooling ──────────────────────────────────────


class TestInstructTemplate:
    def test_none_template_is_bare_text(self) -> None:
        assert format_teacher_input("запрос", None) == "запрос"
        assert format_teacher_input("запрос", "") == "запрос"

    def test_placeholder_form(self) -> None:
        out = format_teacher_input("запрос", "Instruct: найди похожую заметку\nQuery: {text}")
        assert out == "Instruct: найди похожую заметку\nQuery: запрос"

    def test_bare_task_wraps_into_qwen3_canonical_shape(self) -> None:
        out = format_teacher_input("запрос", "Given a note, retrieve similar notes")
        assert out == "Instruct: Given a note, retrieve similar notes\nQuery: запрос"

    def test_braces_in_text_are_not_format_expanded(self) -> None:
        # str.format is NOT used — a text containing braces must pass through.
        out = format_teacher_input("value {x} and {y}", "task {text}")
        assert out == "task value {x} and {y}"


class TestTeacherPoolingDetect:
    def test_qwen3_model_type_detected(self) -> None:
        cfg = types.SimpleNamespace(model_type="qwen3", architectures=["Qwen3ForCausalLM"])
        assert detect_teacher_pooling(types.SimpleNamespace(config=cfg), "any") == "last_token"

    def test_qwen3_embedding_id_detected(self) -> None:
        cfg = types.SimpleNamespace(model_type="", architectures=[])
        model = types.SimpleNamespace(config=cfg)
        assert detect_teacher_pooling(model, "Qwen/Qwen3-Embedding-0.6B") == "last_token"

    def test_bert_class_defaults_to_mean(self) -> None:
        cfg = types.SimpleNamespace(model_type="bert", architectures=["BertModel"])
        model = types.SimpleNamespace(config=cfg)
        assert detect_teacher_pooling(model, "cointegrated/rubert-tiny2") == "mean"


class _FakeTensor:
    """numpy-backed tensor with just enough surface for last_token_pool."""

    def __init__(self, arr: np.ndarray) -> None:
        self._a = np.asarray(arr)

    def __array__(self, dtype: object = None) -> np.ndarray:
        return self._a if dtype is None else self._a.astype(dtype)

    def __getitem__(self, key: object) -> _FakeTensor:
        return _FakeTensor(self._a[key])

    @property
    def shape(self) -> tuple[int, ...]:
        return self._a.shape  # type: ignore[return-value]

    @property
    def device(self) -> str:
        return "cpu"

    def sum(self, dim: int | None = None) -> _FakeTensor:
        return _FakeTensor(self._a.sum(axis=dim) if dim is not None else self._a.sum())

    def __eq__(self, other: object) -> bool:  # type: ignore[override]
        return bool(np.all(self._a == other))

    def __sub__(self, other: object) -> _FakeTensor:
        return _FakeTensor(self._a - other)


class TestLastTokenPool:
    def _run(self, monkeypatch: pytest.MonkeyPatch, mask: list[list[int]]) -> np.ndarray:
        from training.distill import last_token_pool

        monkeypatch.setitem(
            sys.modules,
            "torch",
            types.SimpleNamespace(arange=lambda n, device=None: np.arange(n)),
        )
        hidden = _FakeTensor(np.array([[10.0, 11.0, 12.0, 13.0]]))
        pooled = last_token_pool(hidden, _FakeTensor(np.array(mask)))
        return np.asarray(pooled)

    def test_left_padding_takes_last_position(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = self._run(monkeypatch, mask=[[0, 0, 1, 1]])
        assert out.tolist() == [13.0]

    def test_right_padding_takes_length_minus_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = self._run(monkeypatch, mask=[[1, 1, 0, 0]])
        assert out.tolist() == [11.0]


# ── --from-mnemos-db collector ────────────────────────────────────────────────


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE memories (
            id      TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            title   TEXT,
            tags    TEXT NOT NULL DEFAULT '[]',
            project TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.executemany(
        "INSERT INTO memories (id, content, tags, project) VALUES (?, ?, ?, ?)",
        [
            (
                "m1",
                "Заметка о пуле соединений в API и ретраях. "
                "Длинный первый абзац, чтобы пройти порог в сорок символов.",
                '["project:project-mnemos", "agent:abyss"]',
                "project-mnemos",
            ),
            (
                "m2",
                "Note about connection pooling and backpressure in the queue worker.",
                '["project:project-atlas"]',
                "project-atlas",
            ),
            ("m3", "short", '["project:project-mnemos"]', "project-mnemos"),
            ("m4", "   ", '["project:project-mnemos"]', "project-mnemos"),
        ],
    )
    conn.commit()
    conn.close()


class TestFromMnemosDb:
    def test_reads_content_chunks_with_lang(self, tmp_path: Path) -> None:
        db = tmp_path / "mnemos.db"
        _make_db(db)
        rows = collect_from_mnemos_db(db, limit=100)
        texts = [t for t, _, _ in rows]
        assert any("connection pooling" in t for t in texts)
        assert any("пуле соединений" in t for t in texts)
        assert all(len(t) >= 40 for t in texts)  # short/blank rows never enter
        assert all(src == "mnemos-db:mnemos.db" for _, _, src in rows)
        langs = {lang for _, lang, _ in rows}
        assert langs == {"ru", "en"}

    def test_project_filter(self, tmp_path: Path) -> None:
        db = tmp_path / "mnemos.db"
        _make_db(db)
        only_mnemos = collect_from_mnemos_db(db, limit=100, projects=["project:project-mnemos"])
        only_atlas = collect_from_mnemos_db(db, limit=100, projects=["project-atlas"])
        assert len(only_atlas) == 1 and "queue worker" in only_atlas[0][0]
        assert all("queue worker" not in t for t, _, _ in only_mnemos)
        assert any("пуле соединений" in t for t, _, _ in only_mnemos)

    def test_missing_file_fails_loud(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="not a file"):
            collect_from_mnemos_db(tmp_path / "nope.db", limit=10)

    def test_non_db_file_fails_loud(self, tmp_path: Path) -> None:
        bogus = tmp_path / "bogus.db"
        bogus.write_text("not a database", encoding="utf-8")
        with pytest.raises(SystemExit, match="not a readable mnemos store"):
            collect_from_mnemos_db(bogus, limit=10)

    def test_legacy_schema_without_project_column(self, tmp_path: Path) -> None:
        legacy = tmp_path / "legacy.db"
        conn = sqlite3.connect(legacy)
        conn.execute(
            "CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT NOT NULL, "
            "tags TEXT NOT NULL DEFAULT '[]')"
        )
        conn.execute(
            "INSERT INTO memories (id, content, tags) VALUES ('l1', ?, ?)",
            ("Legacy row about deploy rotation and secrets hygiene, long enough.", '[]'),
        )
        conn.commit()
        conn.close()
        rows = collect_from_mnemos_db(legacy, limit=10)
        assert len(rows) == 1


# ── Export: mrl_dims detection ────────────────────────────────────────────────


class TestExportMrlDims:
    def test_fallback_is_plain_mode(self, tmp_path: Path) -> None:
        contract = detect_mrl_dims(tmp_path, None)
        assert contract == {"embed_dim": DEFAULT_EMBED_DIM, "mrl_dims": [DEFAULT_EMBED_DIM]}

    def test_checkpoint_file_is_read_back(self, tmp_path: Path) -> None:
        import json

        (tmp_path / "mrl_dims.json").write_text(
            json.dumps({"embed_dim": 384, "mrl_dims": [64, 128, 256, 384]}), encoding="utf-8"
        )
        assert detect_mrl_dims(tmp_path, None) == {
            "embed_dim": 384,
            "mrl_dims": [64, 128, 256, 384],
        }

    def test_cli_override_beats_checkpoint_file(self, tmp_path: Path) -> None:
        import json

        (tmp_path / "mrl_dims.json").write_text(
            json.dumps({"embed_dim": 384, "mrl_dims": [64, 128, 256, 384]}), encoding="utf-8"
        )
        assert detect_mrl_dims(tmp_path, "128") == {"embed_dim": 128, "mrl_dims": [128]}

    def test_corrupt_checkpoint_falls_back(self, tmp_path: Path) -> None:
        (tmp_path / "mrl_dims.json").write_text("corrupt{", encoding="utf-8")
        contract = detect_mrl_dims(tmp_path, None)
        assert contract["mrl_dims"] == [DEFAULT_EMBED_DIM]

    def test_bad_cli_spec_fails_loud(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="--mrl-dims"):
            detect_mrl_dims(tmp_path, "384,64")

    def test_default_teacher_is_qwen3(self) -> None:
        # Round-3 contract: the default teacher moved to Qwen3-Embedding.
        assert DEFAULT_TEACHER == "Qwen/Qwen3-Embedding-0.6B"
