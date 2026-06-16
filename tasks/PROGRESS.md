# Progress Log — Mnemos

Living snapshot of the project's state. Append-only; update the
most-recent entry rather than editing history.

---

## 2026-06-16 — PR #2 (M17 CI) green

**What was done**

- PR #2 (`feat/m17-ci-pipeline`) now passes CI end-to-end.
- 4 atomic commits on top of the PR branch:
  1. `890ec86` — initial M17 (CI workflow + Dependabot)
  2. `fdb9f17` — `ruff format` reformat of 24 files (CI fix)
  3. `509c767` — pin ruff to `>=0.15,<0.16` in CI
  4. `e748a70` — `tests/test_cli.py` (13 smoke tests, +2.71pp coverage)
  5. `870af46` — sync `test_cli.py` to ruff 0.15.x format

**Coverage**
- Before: 80.76% (gate passed locally, FAILED in CI at 79%)
- After:  83.47% (gate passes both locally and in CI)
- The hidden gap: `src/mnemos/cli/main.py` was at 0% (111/111 lines)
  in CI. The local measurement was just on the edge.

**CI runs on PR #2**
| Run ID        | Result  | Reason                              |
|---------------|---------|-------------------------------------|
| 27609113824   | failure | ruff format — 24 files (no pin)     |
| 27611781863   | failure | coverage 79% < 80% gate             |
| 27612113136   | failure | format drift on `test_cli.py`       |
| 27612220909   | success | all 4 jobs green                    |

**CI breakdown (27612220909)**
- ✓ Lint + Test + Type + Security (Python 3.11)
- ✓ Lint + Test + Type + Security (Python 3.12)
- ✓ Lint + Test + Type + Security (Python 3.13)
- ✓ Container build smoke

**Branch state**
- `feat/m17-ci-pipeline` at `870af46` (ready to merge)
- `fix/m17-ci-format-and-pin` at same SHA (used for force-push)

**M-series status after PR #2**
- M1-M14, M15, M16, M17 ✅
- M18 (coverage push) ✅ — included in PR #2
- M19 (final code review) ⏳ — next

---

## Pending work (TODO after user merges PR #2)

1. **README rewrite with Mnemos lore** (Greek Titaness of memory,
   mother of the 9 Muses). User explicitly requested.
   - No M1-M15 milestone table in README body
   - Move milestone table to `docs/milestones.md`
   - Banner ASCII + Mermaid diagram
2. **CHANGELOG.md update for v0.2.0**
3. **M19 — final code review** (Code Reviewer agent or manual)
4. **ADR-0013 status: Accepted → Verified** (after M19 passes)
5. **Branch protection on main** (admin-only)

---

## Open questions / decisions needed

- **README structure**: user wants "professional, not bad".
  Need to decide:
  - Where does quickstart go? (top vs separate file)
  - How much architecture? (link to `docs/architecture.md` vs inline)
  - Author voice? (terse vs narrative with myth)
- **v0.2.0 vs v0.1.0**: bump for these changes, or wait until
  M19 review closes the cycle?
