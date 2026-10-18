#!/usr/bin/env python3
"""Fail when a generated machine-readable artifact changes without a version
bump (issue #1409). Policy: docs/interface-versioning-policy.md
"Generated artifact versions".

Each artifact in ARTIFACTS has its version read from the file itself; the
content hash (version field excluded) and version of the last accepted state
live in tools/ci/artifact-versions.lock.json.

  check_artifact_versions.py           verify the working tree against the lock
  check_artifact_versions.py --update  accept the current state into the lock
                                       (refuses content changes without a bump)
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = "tools/ci/artifact-versions.lock.json"

# path -> (kind, version locator). kind "json" locates a top-level key,
# kind "text" a regex whose group 1 is the version.
ARTIFACTS = {
    "schemas/risk_score.schema.json": ("json", "schema_version"),
    "tests/composability/sdk_conformance_fixtures.json": ("json", "schema_version"),
    "tests/conformance/conformance_vectors.json": ("json", "suite_version"),
    "tools/compliance-report/generate.py": ("text", r"^REPORT_VERSION = (\d+)$"),
}


def parse_version(raw):
    """Versions are positive integers or dotted semver; compare as tuples."""
    text = str(raw)
    if not re.fullmatch(r"\d+(\.\d+){0,2}", text):
        raise ValueError(f"unsupported version format {raw!r}")
    return tuple(int(part) for part in text.split("."))


def read_artifact(root, path):
    kind, locator = ARTIFACTS[path]
    raw = (Path(root) / path).read_text()
    if kind == "json":
        doc = json.loads(raw)
        if locator not in doc:
            raise ValueError(f"{path}: missing top-level {locator!r}")
        version = doc.pop(locator)
        body = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    else:
        match = re.search(locator, raw, re.M)
        if not match:
            raise ValueError(f"{path}: version line not found")
        version = match.group(1)
        body = raw[: match.start()] + raw[match.end():]
    parse_version(version)
    return str(version), hashlib.sha256(body.encode()).hexdigest()


def evaluate(root, update=False):
    lock_path = Path(root) / LOCK
    lock = json.loads(lock_path.read_text()) if lock_path.exists() else {}
    errors, new_lock = [], {}
    for path in ARTIFACTS:
        try:
            version, digest = read_artifact(root, path)
        except (OSError, ValueError) as error:
            errors.append(str(error))
            continue
        new_lock[path] = {"version": version, "content_sha256": digest}
        old = lock.get(path)
        if old is None:
            errors.append(f"{path}: not in {LOCK}; run with --update to register")
            continue
        old_v, new_v = parse_version(old["version"]), parse_version(version)
        if new_v < old_v:
            errors.append(f"{path}: version went backwards ({old['version']} -> {version})")
        elif digest != old["content_sha256"] and new_v == old_v:
            errors.append(f"{path}: content changed without a version bump (still {version})")
        elif new_v > old_v and digest == old["content_sha256"]:
            errors.append(f"{path}: version bumped ({old['version']} -> {version}) without a content change")
        elif digest != old["content_sha256"] or new_v != old_v:
            errors.append(f"{path}: bumped to {version}; run with --update to record it")
    stale = sorted(set(lock) - set(ARTIFACTS))
    errors += [f"{path}: in lock but no longer a registered artifact" for path in stale]
    if update and not [e for e in errors if "without a version bump" in e or "backwards" in e or "without a content change" in e]:
        lock_path.write_text(json.dumps(new_lock, indent=2, sort_keys=True) + "\n")
        return []
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args(argv)
    errors = evaluate(ROOT, update=args.update)
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
