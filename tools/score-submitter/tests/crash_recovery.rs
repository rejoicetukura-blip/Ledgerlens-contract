//! Fault-injection harness for crash safety of the simulate -> submit window (#1410).
//!
//! The submitter keeps no on-disk state: its only durable state is the
//! contract's idempotency-key record. A "crash" is therefore modelled by
//! panicking inside a transport call (before or after the call's effect on
//! the simulated chain), discarding the `ScoreSubmitter`, and restarting a
//! fresh one against the same chain. Every transport call in a clean run is a
//! crash point, so the sweep covers the whole simulate-then-submit flow.

use std::cell::RefCell;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::rc::Rc;

use score_submitter::*;

struct Crash;

#[derive(Default)]
struct Chain {
    mempool: Vec<(String, String)>,
    accepted: Vec<(String, String, u32)>,
    rejected_duplicates: Vec<String>,
    ledger: u32,
    sequence: u64,
    /// When false, a restart does not close a ledger, so the lookup is blind
    /// to a transaction submitted before the crash (RPC lag).
    close_on_restart: bool,
    calls: usize,
    last_submit_call: usize,
    crash_at: Option<(usize, bool)>,
}

impl Chain {
    fn close_ledger(&mut self) {
        self.ledger += 1;
        for (hash, key) in std::mem::take(&mut self.mempool) {
            if self.accepted.iter().any(|(_, k, _)| *k == key) {
                self.rejected_duplicates.push(hash);
            } else {
                self.accepted.push((hash, key, self.ledger));
            }
        }
    }
}

#[derive(Clone)]
struct Node(Rc<RefCell<Chain>>);

impl Node {
    /// Registers a transport call; panics when it is the injected crash point.
    /// Returns whether to crash *after* the call's effect.
    fn tick(&self) -> bool {
        let mut chain = self.0.borrow_mut();
        chain.calls += 1;
        match chain.crash_at {
            Some((n, before)) if n == chain.calls => {
                if before {
                    drop(chain);
                    std::panic::panic_any(Crash);
                }
                true
            }
            _ => false,
        }
    }

    fn finish<R>(&self, crash_after: bool, value: R) -> R {
        if crash_after {
            std::panic::panic_any(Crash);
        }
        value
    }
}

type R<T> = std::result::Result<T, TransportError>;

impl SorobanTransport for Node {
    fn next_sequence(&mut self) -> R<u64> {
        let c = self.tick();
        let v = {
            let mut chain = self.0.borrow_mut();
            chain.sequence += 1;
            chain.sequence
        };
        self.finish(c, Ok(v))
    }
    fn transaction_bounds(&mut self) -> R<TransactionBounds> {
        let c = self.tick();
        self.finish(c, Ok(TransactionBounds { min_time: 0, max_time: 100, ledger_sequence: 1 }))
    }
    fn build(
        &mut self,
        request: &ScoreSubmission,
        sequence: u64,
        bounds: TransactionBounds,
        fee: u64,
        resources: Resources,
    ) -> R<Transaction> {
        let c = self.tick();
        let tx = Transaction {
            payload: format!("{}:{}", request.idempotency_key, sequence).into_bytes(),
            sequence,
            fee,
            resources,
            bounds,
            idempotency_key: request.idempotency_key.clone(),
        };
        self.finish(c, Ok(tx))
    }
    fn simulate(&mut self, _: &Transaction) -> R<Simulation> {
        let c = self.tick();
        self.finish(
            c,
            Ok(Simulation {
                minimum_fee: 100,
                required_resources: Resources::default(),
                restore_required: false,
            }),
        )
    }
    fn sign(&mut self, tx: &Transaction) -> R<Vec<u8>> {
        let c = self.tick();
        self.finish(c, Ok(tx.payload.clone()))
    }
    fn submit(&mut self, signed: &[u8]) -> R<String> {
        let c = self.tick();
        let payload = String::from_utf8(signed.to_vec()).unwrap();
        let hash = hex::encode(payload.as_bytes());
        let key = payload.split(':').next().unwrap().to_owned();
        let mut chain = self.0.borrow_mut();
        chain.last_submit_call = chain.calls;
        chain.mempool.push((hash.clone(), key));
        drop(chain);
        self.finish(c, Ok(hash))
    }
    fn confirm(&mut self, hash: &str) -> R<Confirmation> {
        let c = self.tick();
        let mut chain = self.0.borrow_mut();
        chain.close_ledger();
        let result = if let Some((h, k, l)) = chain.accepted.iter().find(|(h, _, _)| h == hash) {
            Ok(Confirmation { transaction_hash: h.clone(), ledger: *l, idempotency_key: k.clone() })
        } else {
            Err(TransportError::new(FailureKind::Permanent, "duplicate idempotency key"))
        };
        drop(chain);
        self.finish(c, result)
    }
    fn lookup_idempotency_key(&mut self, _: &str, key: &str) -> R<Option<Confirmation>> {
        let c = self.tick();
        let found = self.0.borrow().accepted.iter().find(|(_, k, _)| k == key).map(|(h, k, l)| {
            Confirmation { transaction_hash: h.clone(), ledger: *l, idempotency_key: k.clone() }
        });
        self.finish(c, Ok(found))
    }
    fn restore_archived_entries(&mut self, _: &ScoreSubmission) -> R<()> {
        let c = self.tick();
        self.finish(c, Ok(()))
    }
}

fn request() -> ScoreSubmission {
    ScoreSubmission {
        contract_id: "C1".into(),
        wallet: "G1".into(),
        asset_pair: "XLM/USDC".into(),
        score: 700,
        confidence: 90,
        model_version: 1,
        timestamp: 1_700_000_000,
        nonce: 7,
        idempotency_key: String::new(),
    }
    .with_derived_idempotency_key()
    .unwrap()
}

fn config() -> SubmitterConfig {
    SubmitterConfig { base_backoff_ms: 0, max_backoff_ms: 0, ..SubmitterConfig::default() }
}

fn run(node: &Node) -> std::thread::Result<anyhow::Result<Confirmation>> {
    let node = node.clone();
    catch_unwind(AssertUnwindSafe(move || ScoreSubmitter::new(node, config()).submit(&request())))
}

fn fresh(close_on_restart: bool) -> Node {
    Node(Rc::new(RefCell::new(Chain { close_on_restart, ..Chain::default() })))
}

/// Crashes at call `n`, restarts once, and returns the chain.
fn crash_and_restart(n: usize, before: bool, close_on_restart: bool) -> Node {
    let node = fresh(close_on_restart);
    node.0.borrow_mut().crash_at = Some((n, before));
    match run(&node) {
        Err(payload) => assert!(payload.is::<Crash>(), "unexpected panic at call {n}"),
        Ok(result) => {
            // The crash point was past the end of the flow; nothing to restart.
            result.unwrap();
        }
    }
    {
        let mut chain = node.0.borrow_mut();
        chain.crash_at = None;
        if chain.close_on_restart {
            chain.close_ledger();
        }
    }
    let confirmation = run(&node).expect("restart must not crash").expect("restart must converge");
    assert_eq!(confirmation.idempotency_key, request().idempotency_key);
    node
}

fn clean_run_calls() -> usize {
    let node = fresh(true);
    run(&node).unwrap().unwrap();
    let calls = node.0.borrow().calls;
    calls
}

#[test]
fn clean_run_accepts_exactly_one_submission() {
    let node = fresh(true);
    run(&node).unwrap().unwrap();
    assert_eq!(node.0.borrow().accepted.len(), 1);
}

#[test]
fn restart_after_crash_at_every_transport_call_accepts_exactly_one() {
    for n in 1..=clean_run_calls() {
        for before in [true, false] {
            for close_on_restart in [true, false] {
                let node = crash_and_restart(n, before, close_on_restart);
                let accepted = node.0.borrow().accepted.len();
                assert_eq!(
                    accepted,
                    1,
                    "call {n} (crash {}), close_on_restart={close_on_restart}: {accepted} accepted",
                    if before { "before effect" } else { "after effect" },
                );
            }
        }
    }
}

#[test]
fn repeated_restarts_never_double_submit() {
    let probe = fresh(false);
    run(&probe).unwrap().unwrap();
    let submit_offset = probe.0.borrow().last_submit_call;

    let node = fresh(false);
    for _ in 0..3 {
        // Crash right after every submit, before confirmation.
        let base = node.0.borrow().calls;
        node.0.borrow_mut().crash_at = Some((base + submit_offset, false));
        assert!(run(&node).is_err());
    }
    node.0.borrow_mut().crash_at = None;
    run(&node).unwrap().unwrap();
    assert_eq!(node.0.borrow().accepted.len(), 1);
}

/// Regression for the original crash window: the first submission was in the
/// mempool when the process died and the lookup was blind to it. The restart
/// re-submitted, the contract rejected the duplicate key (`Permanent`), and the
/// submitter surfaced an error although the score had been accepted.
#[test]
fn restart_with_blind_lookup_converges_on_the_original_submission() {
    let n = (1..=clean_run_calls())
        .find(|n| {
            let node = fresh(false);
            node.0.borrow_mut().crash_at = Some((*n, false));
            let _ = run(&node);
            let pending = !node.0.borrow().mempool.is_empty();
            pending
        })
        .expect("a crash point after submit must exist");
    let node = crash_and_restart(n, false, false);
    let chain = node.0.borrow();
    assert_eq!(chain.accepted.len(), 1);
    assert_eq!(
        chain.rejected_duplicates.len(),
        1,
        "restart re-submitted and was deduplicated on-chain"
    );
}
