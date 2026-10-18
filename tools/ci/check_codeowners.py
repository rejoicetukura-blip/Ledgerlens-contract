#!/usr/bin/env python3
"""CODEOWNERS staleness check (issue #1408).

Fails when
  * a pattern matches no tracked path (stale after a directory move/rename), or
  * a security- or release-critical path has no owner.

  check_codeowners.py            check .github/CODEOWNERS against `git ls-files`
  check_codeowners.py --self-test  prove a seeded dead pattern is caught
"""

import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODEOWNERS = ".github/CODEOWNERS"

# Paths that must always have a code owner: contracts, cryptography/attestation,
# release engineering, CI and supply-chain configuration. Globs are fnmatch
# patterns over repo-relative paths.
CRITICAL = [
    "contracts/*/src/*.rs",
    "contracts/*/Cargo.toml",
    "schemas/*",
    "fuzz/*",
    "deploy/*",
    "deploy.sh",
    "scripts/*",
    ".github/workflows/*",
    ".github/CODEOWNERS",
    "Cargo.toml",
    "Cargo.lock",
    "deny.toml",
    "rust-toolchain.toml",
    "toolchain-versions.json",
    "coverage-thresholds.toml",
    "test-tiers.toml",
    "SECURITY.md",
    "tools/*",
]


def pattern_regex(pattern):
    """Translate a CODEOWNERS (gitignore-style) pattern to a regex over paths."""
    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    body = pattern.strip("/")
    out, i = "", 0
    while i < len(body):
        if body.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif body.startswith("**", i):
            out, i = out + ".*", i + 2
        elif body[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif body[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(body[i]), i + 1
    prefix = "" if anchored else "(?:.*/)?"
    return re.compile(f"^{prefix}{out}(?:/.*)?$")


def parse(text):
    rules = []
    for line in text.splitlines():
        line = line.split(" #")[0].strip()
        if line and not line.startswith("#"):
            pattern, *owners = line.split()
            rules.append((pattern, owners, pattern_regex(pattern)))
    return rules


def check(rules, files):
    errors = []
    for pattern, owners, regex in rules:
        if not owners:
            errors.append(f"{pattern}: pattern has no owners")
        if not any(regex.match(f) for f in files):
            errors.append(f"{pattern}: matches no tracked path (stale pattern)")
    for glob in CRITICAL:
        for f in sorted(x for x in files if fnmatch(x, glob)):
            owned = [r for r in rules if r[2].match(f)]
            if not owned or not owned[-1][1]:
                errors.append(f"{f}: critical path has no code owner")
    return errors


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True)
    return out.stdout.splitlines()


def self_test():
    files = ["contracts/a/src/lib.rs", "docs/x.md"]
    rules = parse("/contracts/ @o\n/docs/ @o\n/gone-dir/ @o\n")
    errors = check(rules, files)
    assert errors == ["/gone-dir/: matches no tracked path (stale pattern)"], errors
    assert any("no code owner" in e for e in check(parse("/docs/ @o\n"), files))
    print("self-test ok")


def main():
    if "--self-test" in sys.argv:
        self_test()
        return 0
    errors = check(parse((ROOT / CODEOWNERS).read_text()), tracked_files())
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
