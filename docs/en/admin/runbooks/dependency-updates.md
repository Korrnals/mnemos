# Runbook: Dependency Updates & CVE Reminder

**🌐 Language / Язык:** English · [Русский](../../../ru/admin/runbooks/dependency-updates.md)

## Why this exists

`chromadb` currently has ignored `CVE-2026-45829` because there is no upstream fixed release yet.
Mnemos keeps this visible as a warning in `make verify` so we don't forget to upgrade when fix appears.

## Pinning policy (M15.5.1)

Mnemos uses **direct pins** for vulnerable transitives rather than bumping parent packages:

- **`aiohttp>=3.14.1,<4.0`** — direct pin, fixes CVE-2026-34993, 47265, 50269, 54273-54280.
  Pulled in transitively by `chromadb → kubernetes`. Pinning the safe minor directly is
  smaller-blast-radius than bumping `chromadb` (which has no released version that vendors
  patched aiohttp).

- **`starlette>=1.3.0,<2.0`** — direct pin, fixes CVE-2026-48817, 48818, 54282, 54283.
  Pulled in transitively by `fastapi`. Same rationale.

- **`pip` 26.1.2** — upgrade via `pip install --upgrade pip` after venv recreate.
  Fixes PYSEC-2026-196. `pip` is a tool, not a project dep, so it is not in `pyproject.toml`.

- **`chromadb>=0.5`** — left at the existing floor. Bumping chromadb does **not** close the
  aiohttp/starlette CVEs because those packages are not in chromadb's direct dependency set.

When adding a new pin: include a one-line comment in `pyproject.toml` with the CVE id and
the fix version, as in the entries above. Pins must use a range with an upper bound
(`<4.0`, `<2.0`) to prevent accidental major-version drift.

## Daily/weekly quick check

```bash
cd /var/home/abyss/LABs/AI/mnemos
source .venv/bin/activate
make update-chromadb
```

Expected outcomes:
- If no new `chromadb` version exists: nothing changes, keep reminder active.
- If fixed version exists: package upgrades and `pip-audit` should become clean without ignore.

## Full dependency refresh

```bash
cd /var/home/abyss/LABs/AI/mnemos
source .venv/bin/activate
make update-deps
```

Then run full project checks:

```bash
make verify
```

## Remove temporary CVE ignore when fixed

When `pip-audit` confirms fix is available and installed:
1. Edit [Makefile](../../../../Makefile)
2. In target `security`, remove `--ignore-vuln CVE-2026-45829`
3. In target `security-reminder`, remove warning lines
4. Run:

```bash
make verify
```

## Operational policy

- Keep ignore only while upstream has no fix.
- Keep reminder enabled while ignore exists.
- Remove both ignore + reminder in one commit after upgrade.
