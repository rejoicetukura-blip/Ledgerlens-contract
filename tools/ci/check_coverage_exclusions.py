#!/usr/bin/env python3
"""Coverage exclusion safeguard (issue #1406).

The coverage gate skips every path in `exclude` (coverage-thresholds.toml) and
in IGNORE_REGEX (.github/workflows/coverage.yml). This check fails when

  * a tracked source file that is neither structurally test code nor listed in
    ALLOWED_EXCLUSIONS (with a reason) is excluded, or
  * a path matching SECURITY_PATTERN or a `[modules.*]` threshold is excluded
    (allow-listing it still requires editing this file, which CODEOWNERS routes
    to the architecture reviewers), or
  * the two exclusion lists disagree about any tracked .rs file.

  check_coverage_exclusions.py             check the repository
  check_coverage_exclusions.py --self-test prove the safeguard trips
"""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Security-relevant file names: governance, storage, attestation/crypto proofs,
# auth, upgrade, replay protection, oracle input handling.
SECURITY_PATTERN = re.compile(
    r"governance|storage|verkle|zk_|range_proof|attest|auth|crypto|signer|"
    r"upgrade|admin|replay|nonce|oracle|embargo|pause|consensus"
)

# Structural test code: measuring it against itself only inflates numbers.
STRUCTURAL_TEST = re.compile(
    r"(^|/)(tests?|benches|fuzz|test_snapshots|generated)/|(^|/)test(_[^/]*)?\.rs$"
)

# Non-structural exclusions, each with its reviewed justification
# (mirrored in docs/coverage.md). Adding an entry is a reviewed change.
ALLOWED_EXCLUSIONS = {
    "contracts/ledgerlens-score/src/upgrade_smoke.rs":
        "post-upgrade canary tests only; contains no product code",
    "contracts/ledgerlens-test-support/": "test-only helper crate",
    "contracts/mock-amm/": "test-only mock consumer",
    "contracts/mock-lending/": "test-only mock consumer",
}

PRODUCT_ROOTS = re.compile(r"^(contracts/(ledgerlens-score|ledgerlens-aggregator|reference-provider)|tools/[^/]+)/src/")


def load_exclusions(root):
    toml = tomllib.loads((root / "coverage-thresholds.toml").read_text())
    workflow = (root / ".github/workflows/coverage.yml").read_text()
    regex = re.search(r"IGNORE_REGEX:\s*'([^']+)'", workflow).group(1)
    modules = list(toml.get("modules", {}))
    return toml["exclude"], re.compile(regex), modules


def toml_excluded(entries, path):
    return any(("/" + path).find(e) != -1 if e.startswith("/") else path.startswith(e) for e in entries)


def allowed(path):
    return any(path == a or (a.endswith("/") and path.startswith(a)) for a in ALLOWED_EXCLUSIONS)


def check(entries, regex, modules, files):
    errors = []
    for path in sorted(f for f in files if f.endswith(".rs")):
        by_toml, by_regex = toml_excluded(entries, path), bool(regex.search("/" + path))
        if by_toml != by_regex:
            errors.append(
                f"{path}: exclusion lists disagree (coverage-thresholds.toml={by_toml}, "
                f"IGNORE_REGEX={by_regex})"
            )
        if not (by_toml or by_regex) or STRUCTURAL_TEST.search(path) or allowed(path):
            continue
        base = path.rsplit("/", 1)[-1]
        if path in modules or SECURITY_PATTERN.search(base) or PRODUCT_ROOTS.match(path):
            errors.append(
                f"{path}: excluded from coverage but is neither test code nor an "
                f"allow-listed exclusion; see docs/coverage.md#exclusion-policy"
            )
    return errors


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True)
    return out.stdout.splitlines()


def self_test():
    entries, regex, modules = ["tests/", "/generated/"], re.compile(r"/tests/|/generated/"), []
    files = [
        "tests/a.rs",
        "x/generated/b.rs",
        "contracts/ledgerlens-score/src/governance_actions.rs",
        "contracts/ledgerlens-score/src/storage.rs",
    ]
    assert check(entries, regex, modules, files) == []
    # Excluding a security-looking path is caught, even when both lists agree.
    for seeded in ("contracts/ledgerlens-score/src/governance_actions.rs", "contracts/ledgerlens-score/src/storage.rs"):
        errors = check(entries + [seeded], re.compile(regex.pattern + "|" + re.escape(seeded)), modules, files)
        assert any(seeded in e and "allow-listed" in e for e in errors), errors
    # Exclusion-list drift is caught.
    assert any("disagree" in e for e in check(entries + ["x/"], regex, modules, files + ["x/c.rs"]))
    # A `[modules.*]` threshold file cannot be excluded either.
    sneaky = "contracts/ledgerlens-score/src/lib.rs"
    errors = check(entries + [sneaky], re.compile(regex.pattern + "|" + re.escape(sneaky)), [sneaky], files + [sneaky])
    assert any(sneaky in e for e in errors), errors
    print("self-test ok")


def main():
    if "--self-test" in sys.argv:
        self_test()
        return 0
    errors = check(*load_exclusions(ROOT), tracked_files())
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
