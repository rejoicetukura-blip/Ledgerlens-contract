# Score Submitter Reference Pipeline

`score-submitter` provides the serial transaction orchestration for a
retry-safe Soroban score writer. A network-specific `SorobanTransport` adapter
must implement transaction construction, simulation, signing, RPC submission,
confirmation, idempotency lookup, sequence reads, and restore transactions.

The pipeline simulates, adds configurable safety margins to fee and resources,
re-simulates the adjusted transaction, signs and submits it, then waits for
confirmation. It classifies insufficient fee, resource limits, bad sequence,
expired bounds, restore-required, and RPC-lag outcomes. Retries are bounded and
use capped exponential backoff. After an ambiguous RPC response it queries the
contract's idempotency key before deciding whether another submission is safe.
Calls through one `ScoreSubmitter` instance are sequential, preserving sequence
ordering. Metrics are available through `metrics()` and each transition emits
one JSON structured log record to stderr.

Callers supply a stable `idempotency_key` and nonce for the contract request.
`ScoreSubmission::with_derived_idempotency_key` produces a SHA-256 key from the
canonical request fields when the service needs one. The transport must map
these fields onto the deployed contract's supported idempotency and nonce
mechanism; it must not treat an RPC transaction hash alone as the idempotency
record.

`classify_rpc_error` maps common textual RPC/contract failure descriptions to
the retry categories. Transports with structured Horizon or RPC error codes
should prefer their typed mapping and preserve the original code/message in
`TransportError` for diagnostics.

This repository does not currently include a Stellar RPC SDK or a concrete
network adapter. The library is the fault-handling reference core, not a
standalone network submitter. A production adapter must use the official
simulation response and transaction envelope types, and its integration suite
must inject the classified failures before production use.

## Crash safety (simulate → submit)

The submitter holds no durable state of its own; the contract's idempotency-key
record is the only source of truth. After a process crash at **any** transport
call (before or after its effect), a restarted `submit` for the same request
first looks the key up, and otherwise re-submits. The chain deduplicates by
key, and a duplicate-key rejection (`Permanent`) is resolved by a final key
lookup so the restart converges on the original confirmation instead of
failing.

Guarantee: for one `idempotency_key`, at most one submission is accepted
on-chain, regardless of crashes or restarts. Boundaries: the key must be
stable across restarts (use `with_derived_idempotency_key` or persist it in the
caller); the guarantee relies on the contract rejecting duplicate keys; it
does not cover a caller that changes any canonical request field (a different
key is a different request). `tests/crash_recovery.rs` sweeps every transport
call as a crash point, with and without RPC lag.
