# Code Coverage

Coverage is measured with [cargo-llvm-cov](https://github.com/taiki-e/cargo-llvm-cov)
and gated per module (issue #1225). Coverage is one signal; mutation testing
([mutation-testing.md](mutation-testing.md)) is the other and checks that the
covered lines are actually asserted on.

## Running locally

```bash
rustup component add llvm-tools-preview
cargo install cargo-llvm-cov --version 0.6.16 --locked

IGNORE='(/src/test[^/]*\.rs$|/src/upgrade_smoke\.rs$|/tests/|/benches/|/fuzz/|/generated/|ledgerlens-test-support/|mock-amm/|mock-lending/)'
cargo llvm-cov --workspace --no-report
cargo llvm-cov report --ignore-filename-regex "$IGNORE" --html      # browse target/llvm-cov/html/index.html
cargo llvm-cov report --ignore-filename-regex "$IGNORE" --json --output-path target/coverage.json
python3 tools/ci/coverage_gate.py target/coverage.json               # same gate as CI

# Only one crate or one test module
cargo llvm-cov -p ledgerlens-score --html -- test_governance_chain
```

## What is measured and what is excluded

Contract crates (`ledgerlens-score`, `ledgerlens-aggregator`) and tools are
measured. Excluded, both in the `--ignore-filename-regex` and in the
`exclude` list of [`coverage-thresholds.toml`](../coverage-thresholds.toml):

| Excluded | Why |
|---|---|
| `src/test*.rs` (`test.rs`, `test_*.rs`) in `ledgerlens-score` and `ledgerlens-aggregator`, `tests/` | test code measuring itself inflates numbers |
| `contracts/ledgerlens-score/src/upgrade_smoke.rs` | post-upgrade canary tests only; no product code |
| `benches/`, `fuzz/` | measurement/fuzzing harnesses, not product code |
| `generated/`, `test_snapshots/` paths | generated code and recorded snapshots |
| `ledgerlens-test-support`, `mock-amm`, `mock-lending` | test-only crates |

### Exclusion policy

Exclusions are a blind spot: a path on the list stops being gated. So:

1. **Only test, generated or harness code may be excluded.** Anything else
   needs an entry in `ALLOWED_EXCLUSIONS` in
   [`tools/ci/check_coverage_exclusions.py`](../tools/ci/check_coverage_exclusions.py)
   with a written reason, and that row must be added to the table above.
2. **Security-relevant files can never be silently excluded.** The check fails
   when an excluded file is product code under `contracts/*/src` or
   `tools/*/src`, its name matches governance / storage / attestation / auth /
   upgrade / replay / oracle patterns, or it has a `[modules.*]` threshold.
3. **The two lists must agree.** `exclude` in `coverage-thresholds.toml` and
   `IGNORE_REGEX` in `.github/workflows/coverage.yml` (and the `IGNORE` example
   above) must exclude exactly the same `.rs` files; otherwise the local report,
   the CI report and the gate disagree about what is measured.
4. **Changes are flagged in review.** `coverage-thresholds.toml`,
   `.github/` and `tools/ci/` are in `CODEOWNERS`, so architecture reviewers
   must approve; on PRs the `Coverage` workflow also emits a warning annotation
   and prints the diff of the exclusion/gate configuration in the job summary.

The check runs in CI (`repo-hygiene` job and the `Coverage` workflow) and
locally with `python3 tools/ci/check_coverage_exclusions.py`;
`--self-test` demonstrates that seeded security-looking exclusions
(`governance_actions.rs`, `storage.rs`, a `[modules.*]` file) and list drift are
rejected.

#### Audit (2026-10)

Every current exclusion is test code, a harness, a snapshot/generated output or
a test-only crate (table above). The audit found and fixed two inconsistencies:
the TOML list did not exclude the aggregator's `test*.rs` or nested `benches/`
(while the regex did), and `upgrade_smoke.rs` was excluded in the TOML list but
not in the regex.

## Thresholds

Defined in `coverage-thresholds.toml`; the longest matching path prefix wins.

| Scope | Line | Branch (nightly) |
|---|---|---|
| Governance (`governance_actions.rs`, `governance_helpers.rs`, `parameter_governance.rs`) | 85% | 70% |
| Storage (`storage.rs`) | 85% | 70% |
| Attestation proofs (`zk_range_proof.rs`, `verkle.rs`) | 85% | 70% |
| Entry points and authorisation (`lib.rs`) | 80% | 65% |
| Aggregator contract | 75% | – |
| Tools | 40% | – |
| Everything else | 50% | – |

**No-decrease rule:** on pull requests, every changed file must keep at least
the line coverage it had in the latest `main` report
(`max_changed_file_drop = 0.0`).

A threshold may only be lowered with a justification in the PR; raise it
whenever a module comfortably exceeds it so gains are locked in.

## Reading CI output

The `Coverage` workflow:

* writes a per-module table (files, line %, branch %, threshold) to the job
  summary and, for same-repository PRs, as a sticky PR comment (fork PRs get
  a read-only token, so they see the job summary only);
* uploads `coverage-report` (`coverage.json`, `coverage.lcov`,
  `coverage-summary.md`, 30 days). Load the lcov file in an editor plugin
  such as Coverage Gutters to see uncovered lines inline;
* lists every violation as an error annotation and fails.

Branch coverage needs a nightly toolchain, so it runs in the scheduled
`Branch coverage (nightly)` job, which also enforces the `branch` thresholds.
It is informative (`continue-on-error`) until nightly Soroban builds are stable.

Reports are reproducible: the toolchain (1.81.0), `cargo-llvm-cov` version and
`Cargo.lock` are pinned, and the build cache key includes all three.

## Improving coverage

1. Open the HTML report and find red lines in the module that failed.
2. Prefer tests that assert behaviour (errors returned, events emitted,
   storage written) over tests that only execute code; mutation testing will
   flag the latter.
3. For security-critical modules, cover each rejection path (unauthorised
   caller, stale or replayed attestation, bad proof) with its own test.
4. Re-run the gate locally before pushing.

## Coverage and mutation testing

Covered-but-unasserted code is invisible to coverage. The security-critical
modules above are the same ones targeted by the mutation shards in
`.github/workflows/mutation.yml`. When a module passes the coverage gate but
has surviving mutants, the mutants win: add assertions until they are killed.
