#!/usr/bin/env node
// Sync package.json `version` from pyproject.toml (single source of truth).
// Run from anywhere: `node scripts/sync-version.mjs`. Idempotent.
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const pyproject = fs.readFileSync(path.join(root, "pyproject.toml"), "utf8");
const m = pyproject.match(/^version\s*=\s*"([^"]+)"/m);
if (!m) {
  console.error("sync-version: could not find `version = \"…\"` in pyproject.toml");
  process.exit(1);
}
const version = m[1];

const pjPath = path.join(root, "package.json");
const pj = JSON.parse(fs.readFileSync(pjPath, "utf8"));
if (pj.version === version) {
  console.log(`sync-version: package.json already at ${version}`);
  process.exit(0);
}
pj.version = version;
fs.writeFileSync(pjPath, JSON.stringify(pj, null, 2) + "\n");
console.log(`sync-version: package.json version → ${version}`);