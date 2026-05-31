"""M10 — Context Filter pipeline.

5-stage filtering that sits between raw input and model-facing flows:
  1. dedup    — exact + near-duplicate line suppression
  2. noise    — ANSI/progress/timestamps/separators cleanup
  3. extract  — errors/warnings/exit-status + informative sampling
  4. compress — semantic compression of repetitive blocks
  5. tokens   — pre-tokenization estimation and budget accounting

Profiles: log | terminal | code | docs | web | default
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Stage 1: dedup ───────────────────────────────────────────────────────────


def _stage_dedup(text: str, threshold: float = 0.9) -> tuple[str, dict[str, Any]]:
    """Remove exact and near-duplicate lines.

    Returns (deduped_text, stats).
    """
    lines = text.splitlines()
    if not lines:
        return text, {"lines_in": 0, "lines_out": 0, "exact_dups": 0, "near_dups": 0}

    seen: set[str] = set()
    out_lines: list[str] = []
    exact_dups = 0
    near_dups = 0

    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            out_lines.append(line)
            continue

        # Exact duplicate
        if stripped in seen:
            exact_dups += 1
            continue

        # Near-duplicate: same normalized form (alphanumeric only)
        normalized = re.sub(r"\W+", "", stripped).lower()
        if normalized and normalized in seen:
            near_dups += 1
            continue

        seen.add(stripped)
        if normalized:
            seen.add(normalized)
        out_lines.append(line)

    stats = {
        "lines_in": len(lines),
        "lines_out": len(out_lines),
        "exact_dups": exact_dups,
        "near_dups": near_dups,
    }
    return "\n".join(out_lines), stats


# ── Stage 2: noise ───────────────────────────────────────────────────────────

# ANSI escape codes
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# Progress bars / spinners
_PROGRESS_RE = re.compile(
    r"[#\-=▶◀◐◑◒◓\|\\/]+\s*\d+%?"
    r"|\[\s*#+\s*\]\s*\d*%?"
    r"|\[\s*\d+/\d*\s*\]"
)
# Timestamps (ISO, common log formats)
_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\s*"
)
# Separator lines
_SEPARATOR_RE = re.compile(r"^[-=]{3,}$|^\s*[*•]\s*$", re.MULTILINE)


def _stage_noise(text: str, profile: str) -> tuple[str, dict[str, Any]]:
    """Clean up noise patterns based on profile.

    Returns (cleaned_text, stats).
    """
    original_len = len(text)
    removed_ansi = 0
    removed_progress = 0
    removed_timestamps = 0
    removed_separators = 0

    # ANSI codes (all profiles)
    text, count = _ANSI_RE.subn("", text)
    removed_ansi = count

    if profile in ("log", "terminal"):
        # Timestamps first (before progress bars that might corrupt dates)
        text, count = _TIMESTAMP_RE.subn("", text)
        removed_timestamps = count

        # Progress bars
        text, count = _PROGRESS_RE.subn("", text)
        removed_progress = count

    if profile in ("log", "terminal", "default"):
        # Separator lines
        lines = text.splitlines()
        out_lines = []
        for line in lines:
            if _SEPARATOR_RE.match(line.strip()):
                removed_separators += 1
                continue
            out_lines.append(line)
        text = "\n".join(out_lines)

    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    stats = {
        "original_chars": original_len,
        "removed_ansi": removed_ansi,
        "removed_progress": removed_progress,
        "removed_timestamps": removed_timestamps,
        "removed_separators": removed_separators,
    }
    return text, stats


# ── Stage 3: extract ─────────────────────────────────────────────────────────

# Error/warning patterns
_ERROR_RE = re.compile(
    r"(?:error|exception|traceback|failed|failure|fatal|panic|assertionerror|"
    r"runtimeerror|typeerror|valueerror|keyerror|indexerror)\s*:?\s*",
    re.IGNORECASE,
)
_WARNING_RE = re.compile(
    r"(?:warning|warn|deprecated|obsolete|todo|fixme|hack|xxx)\s*:?\s*",
    re.IGNORECASE,
)
_EXIT_RE = re.compile(
    r"(?:exit\s*code|return\s*code|status\s*code|rc=|exited\s*with(?:\s*code)?)"
    r"\s*:?\s*\d+",
    re.IGNORECASE,
)


def _stage_extract(text: str, profile: str) -> tuple[str, dict[str, Any]]:
    """Extract signal-rich lines and sample context.

    Returns (extracted_text, stats).
    """
    lines = text.splitlines()
    if not lines:
        return text, {"signal_lines": 0, "sampled": 0}

    signal_lines: list[tuple[int, str]] = []  # (idx, line)
    sampled = 0

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        # Signal detection
        is_signal = bool(
            _ERROR_RE.search(stripped)
            or _WARNING_RE.search(stripped)
            or _EXIT_RE.search(stripped)
        )

        if is_signal:
            # Include context: 2 lines before and after
            start = max(0, idx - 2)
            end = min(len(lines), idx + 3)
            for i in range(start, end):
                if (i, lines[i]) not in signal_lines:
                    signal_lines.append((i, lines[i]))

    if signal_lines:
        # Sort by original index and reconstruct
        signal_lines.sort(key=lambda x: x[0])
        out = "\n".join(line for _, line in signal_lines)
        stats = {"signal_lines": len(signal_lines), "sampled": len(signal_lines)}
        return out, stats

    # No signals found — return original with sampling for long content
    if len(lines) > 50 and profile in ("log", "terminal"):
        # Keep first 10, last 10, and every 10th in between
        keep = set(range(10))
        keep.update(range(len(lines) - 10, len(lines)))
        for i in range(10, len(lines) - 10, 10):
            keep.add(i)
        out_lines = [lines[i] for i in sorted(keep)]
        sampled = len(lines) - len(out_lines)
        stats = {"signal_lines": 0, "sampled": sampled}
        return "\n".join(out_lines), stats

    return text, {"signal_lines": 0, "sampled": 0}


# ── Stage 4: compress ────────────────────────────────────────────────────────


def _stage_compress(text: str, profile: str) -> tuple[str, dict[str, Any]]:
    """Compress repetitive blocks (stack traces, repeated patterns).

    Returns (compressed_text, stats).
    """
    lines = text.splitlines()
    if len(lines) < 10:
        return text, {"original_lines": len(lines), "compressed_blocks": 0}

    # Detect repeated block patterns (e.g., stack trace frames)
    out_lines: list[str] = []
    compressed_blocks = 0
    i = 0

    while i < len(lines):
        line = lines[i]
        # Look for repeated patterns (same first two words)
        words = line.strip().split()
        if len(words) >= 2 and i + 2 < len(lines):
            prefix = f"{words[0]} {words[1]}"
            run = [line]
            j = i + 1
            while j < len(lines):
                j_words = lines[j].strip().split()
                if len(j_words) >= 2 and f"{j_words[0]} {j_words[1]}" == prefix:
                    run.append(lines[j])
                    j += 1
                else:
                    break

            if len(run) >= 3:
                out_lines.append(f"  ... ({len(run)} similar lines) ...")
                compressed_blocks += 1
                i = j
                continue

        out_lines.append(line)
        i += 1

    result = "\n".join(out_lines)
    stats = {
        "original_lines": len(lines),
        "compressed_lines": len(out_lines),
        "compressed_blocks": compressed_blocks,
    }
    return result, stats


# ── Stage 5: tokens ────────────────────────────────────────────────────────────


def _estimate_tokens(text: str) -> int:
    """Rough token estimation: ~4 chars per token for English/code."""
    if not text:
        return 0
    # Count words and punctuation
    words = len(text.split())
    chars = len(text)
    # Heuristic: average token is ~4 chars for code/English
    return max(words, chars // 4)


def _stage_tokens(text: str, budget: int | None = None) -> tuple[str, dict[str, Any]]:
    """Estimate tokens and optionally truncate to budget.

    Returns (text, stats).
    """
    estimated = _estimate_tokens(text)
    stats = {
        "estimated_tokens": estimated,
        "budget": budget,
        "truncated": False,
    }

    if budget and estimated > budget and text:
        # Truncate by characters (rough approximation)
        target_chars = budget * 4
        truncated_text = text[:target_chars]
        # Try to end at a line boundary
        last_nl = truncated_text.rfind("\n")
        if last_nl > target_chars * 0.8:
            truncated_text = truncated_text[:last_nl]
        truncated_text += "\n\n[...truncated...]"
        stats["truncated"] = True
        stats["estimated_tokens_after"] = _estimate_tokens(truncated_text)
        return truncated_text, stats

    return text, stats


# ── Profile detection ─────────────────────────────────────────────────────────


def detect_profile(text: str, hint: str | None = None) -> str:
    """Heuristic profile detection from content.

    Returns one of: log, terminal, code, docs, web, default
    """
    if hint:
        hint_lower = hint.lower()
        if hint_lower in ("log", "terminal", "code", "docs", "web", "default"):
            return hint_lower

    text_lower = text.lower()
    lines = text.splitlines()

    # Log detection
    log_indicators = 0
    for line in lines[:20]:
        if re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", line):
            log_indicators += 1
        if re.search(r"\[(?:debug|info|warn|error|fatal)\]", line, re.IGNORECASE):
            log_indicators += 1
    if log_indicators >= 3:
        return "log"

    # Terminal detection
    if "\x1b[" in text or any(c in text for c in "▶◀◐◑◒◓"):
        return "terminal"

    # Code detection
    code_patterns = [
        r"^\s*def\s+",
        r"^\s*class\s+",
        r"^\s*import\s+",
        r"^\s*#\s*include",
        r"^\s*function\s+",
        r"^\s*const\s+",
        r"^\s*let\s+",
        r"^\s*var\s+",
    ]
    code_hits = sum(
        1 for pat in code_patterns for line in lines if re.search(pat, line)
    )
    if code_hits >= 3:
        return "code"

    # Web detection
    if "<html" in text_lower or "<!doctype" in text_lower or "<div" in text_lower:
        return "web"

    # Docs detection
    if text.count("#") > 3 or text.count("---") > 1:
        return "docs"

    return "default"


# ── Main pipeline ────────────────────────────────────────────────────────────


def apply_filter(
    text: str,
    *,
    profile: str | None = None,
    budget: int | None = None,
    version: str = "v1",
) -> dict[str, Any]:
    """Run the full 5-stage context filter pipeline.

    Args:
        text: Raw content to filter.
        profile: One of log | terminal | code | docs | web | default.
                 If None, auto-detected from content.
        budget: Optional token budget for truncation.
        version: Pipeline version string for tracking.

    Returns:
        Dict with:
          - clean_content: str
          - profile: str
          - stats: dict (per-stage metrics)
          - version: str
    """
    detected = profile or detect_profile(text, hint=profile)
    logger.debug("apply_filter: profile=%s budget=%s", detected, budget)

    stats: dict[str, Any] = {"profile": detected}

    # Stage 1: dedup
    text, dedup_stats = _stage_dedup(text)
    stats["dedup"] = dedup_stats

    # Stage 2: noise
    text, noise_stats = _stage_noise(text, detected)
    stats["noise"] = noise_stats

    # Stage 3: extract
    text, extract_stats = _stage_extract(text, detected)
    stats["extract"] = extract_stats

    # Stage 4: compress
    text, compress_stats = _stage_compress(text, detected)
    stats["compress"] = compress_stats

    # Stage 5: tokens
    text, token_stats = _stage_tokens(text, budget)
    stats["tokens"] = token_stats

    # Overall reduction
    stats["reduction"] = {
        "original_chars": noise_stats.get("original_chars", len(text)),
        "final_chars": len(text),
        "char_ratio": len(text) / max(noise_stats.get("original_chars", 1), 1),
    }

    return {
        "clean_content": text,
        "profile": detected,
        "stats": stats,
        "version": version,
    }
