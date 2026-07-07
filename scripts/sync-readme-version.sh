#!/usr/bin/env bash
# sync-readme-version.sh — update version-pinned URLs and tags in README files.
#
# Called by the release workflow after a tag is pushed.  Reads the version
# from pyproject.toml (single source of truth) and replaces every occurrence
# of an old version inside <!-- version:xxx --> … <!-- /version:xxx --> blocks.
#
# Supported marker blocks:
#   <!-- version:pip -->    — pip install URL with wheel filename
#   <!-- version:image -->  — container image tag
#   <!-- version:tags -->   — inline tag references in prose
#
# Usage:
#   scripts/sync-readme-version.sh            # auto-detect version from pyproject.toml
#   scripts/sync-readme-version.sh 2.6.0      # explicit new version
#
# Exit codes: 0 on success, 1 if version cannot be determined or no files match.

set -euo pipefail

# ── locate project root ──────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# ── determine new version ────────────────────────────────────────────
if [[ $# -ge 1 ]]; then
  NEW_VERSION="$1"
else
  NEW_VERSION=$(grep -m1 '^version' pyproject.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
fi

if [[ -z "$NEW_VERSION" ]]; then
  echo "ERROR: cannot determine version from pyproject.toml" >&2
  exit 1
fi

echo "Syncing README version → $NEW_VERSION"

# ── files to update ──────────────────────────────────────────────────
FILES=("README.md" "README.ru.md")

# Strict semver: X.Y.Z only (no pre-release suffix — avoids matching
# "2.3.0-py3-none-any" as a version with a pre-release part).
VERSION_RE='[0-9]+\.[0-9]+\.[0-9]+'

changed=0

for file in "${FILES[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "  skip: $file (not found)"
    continue
  fi

  original=$(cat "$file")

  # Replace version strings inside marker blocks.
  # Two passes: first v-prefixed (v2.6.0 → v2.7.0), then bare (2.6.0 → 2.7.0).
  # \b won't work for v-prefixed versions (v and 2 are both word chars),
  # so we match v?X.Y.Z explicitly.
  perl -0777 -i -pe "
    s{(<!--\s*version:[a-z-]+\s*-->.*?<!--\s*/version:[a-z-]+\s*-->)}
     {
       my \$block = \$1;
       \$block =~ s{v${VERSION_RE}}{v${NEW_VERSION}}g;
       \$block =~ s{${VERSION_RE}}{${NEW_VERSION}}g;
       \$block;
     }gse
  " "$file"

  if [[ "$(cat "$file")" != "$original" ]]; then
    echo "  updated: $file"
    changed=$((changed + 1))
  else
    echo "  no change: $file"
  fi
done

if [[ $changed -eq 0 ]]; then
  echo "WARNING: no files were modified — check that version markers exist" >&2
fi

echo "Done. $changed file(s) updated to $NEW_VERSION."