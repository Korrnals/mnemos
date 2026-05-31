"""Tests for M10 — Context Filter pipeline."""

from __future__ import annotations

from mnemos.filter.pipeline import (
    _stage_compress,
    _stage_dedup,
    _stage_extract,
    _stage_noise,
    _stage_tokens,
    apply_filter,
    detect_profile,
)


class TestStageDedup:
    def test_removes_exact_duplicates(self):
        text = "line a\nline b\nline a\nline c"
        result, stats = _stage_dedup(text)
        assert "line a" in result
        assert "line b" in result
        assert "line c" in result
        assert result.count("line a") == 1
        assert stats["exact_dups"] == 1

    def test_removes_near_duplicates(self):
        text = "Error: foo\nError: bar\nError: foo"
        result, stats = _stage_dedup(text)
        assert result.count("Error: foo") == 1
        assert stats["exact_dups"] == 1

    def test_preserves_empty_lines(self):
        text = "line a\n\nline b"
        result, stats = _stage_dedup(text)
        assert "\n\n" in result
        assert stats["exact_dups"] == 0

    def test_empty_input(self):
        result, stats = _stage_dedup("")
        assert result == ""
        assert stats["lines_in"] == 0


class TestStageNoise:
    def test_removes_ansi_codes(self):
        text = "\x1b[31mred text\x1b[0m normal"
        result, stats = _stage_noise(text, "terminal")
        assert "\x1b[" not in result
        assert stats["removed_ansi"] == 2

    def test_removes_progress_bars(self):
        text = "[####      ] 40%\nDone"
        result, stats = _stage_noise(text, "terminal")
        assert "40%" not in result
        assert stats["removed_progress"] == 1

    def test_removes_timestamps(self):
        text = "2024-01-15T10:30:00Z Starting process\nDone"
        result, stats = _stage_noise(text, "log")
        assert "2024-01-15" not in result
        assert stats["removed_timestamps"] == 1

    def test_removes_separators(self):
        text = "Header\n---\nContent"
        result, stats = _stage_noise(text, "log")
        assert "---" not in result
        assert stats["removed_separators"] == 1

    def test_collapses_blank_lines(self):
        text = "a\n\n\n\nb"
        result, _ = _stage_noise(text, "default")
        assert "\n\n\n" not in result
        assert "a\n\nb" in result

    def test_preserves_content_in_code_profile(self):
        text = "Header\n---\nContent"
        result, _stats = _stage_noise(text, "code")
        # Separators are NOT removed in code profile (only log/terminal/default)
        assert "---" in result


class TestStageExtract:
    def test_extracts_error_lines(self):
        text = "line 1\nError: something broke\nline 3"
        result, stats = _stage_extract(text, "log")
        assert "Error: something broke" in result
        assert stats["signal_lines"] > 0

    def test_extracts_warning_lines(self):
        text = "line 1\nWarning: deprecated\nline 3"
        result, stats = _stage_extract(text, "log")
        assert "Warning: deprecated" in result
        assert stats["signal_lines"] > 0

    def test_extracts_exit_codes(self):
        text = "line 1\nExited with code 1\nline 3"
        result, stats = _stage_extract(text, "log")
        assert "Exited with code 1" in result
        assert stats["signal_lines"] > 0

    def test_includes_context_around_signals(self):
        text = "before\nline 1\nError: broke\nline 3\nafter"
        result, _stats = _stage_extract(text, "log")
        assert "before" in result
        assert "Error: broke" in result
        assert "after" in result

    def test_no_signals_returns_original(self):
        text = "Just normal content\nMore content"
        result, stats = _stage_extract(text, "log")
        assert result == text
        assert stats["signal_lines"] == 0

    def test_sampling_for_long_terminal_output(self):
        lines = [f"line {i}" for i in range(100)]
        text = "\n".join(lines)
        result, stats = _stage_extract(text, "terminal")
        assert stats["sampled"] > 0
        assert len(result.splitlines()) < 100


class TestStageCompress:
    def test_compresses_repeated_blocks(self):
        # Need 10+ lines to trigger compression
        text = "\n".join(
            ["  at module func1()"] * 5 + ["  at module func2()"] * 5 + ["Done"]
        )
        result, _stats = _stage_compress(text, "log")
        assert "similar lines" in result or len(result.splitlines()) < 11

    def test_short_text_unchanged(self):
        text = "line 1\nline 2"
        result, stats = _stage_compress(text, "default")
        assert result == text
        assert stats["compressed_blocks"] == 0


class TestStageTokens:
    def test_estimates_tokens(self):
        text = "Hello world this is a test"
        _result, stats = _stage_tokens(text)
        assert stats["estimated_tokens"] > 0

    def test_truncates_to_budget(self):
        text = "word " * 1000
        result, stats = _stage_tokens(text, budget=50)
        assert stats["truncated"] is True
        assert "[...truncated...]" in result
        assert stats["estimated_tokens_after"] <= 55  # some margin

    def test_no_budget_no_truncation(self):
        text = "word " * 100
        result, stats = _stage_tokens(text)
        assert stats["truncated"] is False
        assert result == text


class TestDetectProfile:
    def test_detects_log(self):
        text = "2024-01-15T10:30:00Z [INFO] start\n2024-01-15T10:30:01Z [ERROR] fail"
        assert detect_profile(text) == "log"

    def test_detects_terminal(self):
        text = "\x1b[31mred\x1b[0m"
        assert detect_profile(text) == "terminal"

    def test_detects_code(self):
        text = "def hello():\n    pass\nclass World:\n    pass\nimport os"
        assert detect_profile(text) == "code"

    def test_detects_web(self):
        text = "<html><body>hello</body></html>"
        assert detect_profile(text) == "web"

    def test_detects_docs(self):
        text = "# Title\n## Section\n### Sub\n---\nContent"
        assert detect_profile(text) == "docs"

    def test_defaults_to_default(self):
        text = "Just some plain text."
        assert detect_profile(text) == "default"

    def test_respects_hint(self):
        text = "Just some plain text."
        assert detect_profile(text, hint="log") == "log"


class TestApplyFilter:
    def test_full_pipeline(self):
        text = """2024-01-15T10:30:00Z [INFO] start
2024-01-15T10:30:00Z [INFO] start
\x1b[31mError: something broke\x1b[0m
2024-01-15T10:30:02Z [INFO] done
"""
        result = apply_filter(text, profile="log", budget=100)

        assert result["clean_content"] is not None
        assert result["profile"] == "log"
        assert "stats" in result
        assert result["version"] == "v1"

        # Verify ANSI removed
        assert "\x1b[" not in result["clean_content"]

        # Verify dedup happened (duplicate "start" line)
        assert result["stats"]["dedup"]["exact_dups"] >= 1

        # Verify noise removed
        assert result["stats"]["noise"]["removed_ansi"] > 0

        # Verify signal extracted
        assert result["stats"]["extract"]["signal_lines"] > 0

    def test_auto_detects_profile(self):
        text = "def hello():\n    pass\nclass World:\n    pass\nimport os"
        result = apply_filter(text)
        assert result["profile"] == "code"

    def test_budget_truncation(self):
        text = "word " * 1000
        result = apply_filter(text, budget=50)
        assert result["stats"]["tokens"]["truncated"] is True
        assert "[...truncated...]" in result["clean_content"]

    def test_empty_input(self):
        result = apply_filter("")
        assert result["clean_content"] == ""
        assert result["profile"] == "default"
