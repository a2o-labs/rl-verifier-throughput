# rl-verifier-throughput — the unmeasured bottleneck under the RL-with-verifiers loop

As RL-with-verifiers scales (2026 consensus), the bottleneck is **verification throughput** (Lean / exec
sandbox) — a CPU + scheduling problem nobody benchmarks on-prem. This repo measures it and (next) hardens it
on commodity/sovereign hardware, **as the infra UNDER the loop — never a prover, never "we do AI4Math".**

## P0 result — verify-deployability scorecard v0.2 (kill the tail, not add cores)
Kimina Lean Server 2.0.0 (prebuilt, Mathlib baked in) on **an 8-core native commodity box (on-prem)**, driven
with the first 100 of `Goedel-LM/Lean-workbook-proofs`. Full writeup: [`SCORECARD-v0.2.md`](SCORECARD-v0.2.md)
(v0.1 + its [`REVIEW-v0.1.md`](REVIEW-v0.1.md) kept for the audit trail). Raw: `v0.2-A-corpus.json` /
`v0.2-B-finite.json` (+ `.rows.jsonl` per-proof).

**The bottleneck is a small tail of long-running proofs, and the cheap fixes are a deadline / a finite
`maxHeartbeats` — not more cores (all measured):**
- **The tail:** single-thread, p50 **2.3 s**, but **4% of proofs time out (≥190 s) and eat 52% of the serial
  wall** (0.069 proofs/s). Every miss is a timeout, not a Lean error.
- **Lever 1 — per-proof deadline** (post-hoc over captured latencies): **30 s → 0.069→0.158 proofs/s (2.3×)**,
  single-thread, zero infra change (10 s → 0.260).
- **Lever 2 — finite Lean `maxHeartbeats`=200000** (A/B): kills the tail in-kernel (timeout 4%→0), C=1
  0.069→0.109, **peak ~0.18→~0.34 (≈1.9×)**. *Honest tradeoff:* pass-rate 96%→93% (~3 pp of legit proofs
  needing >200 k heartbeats are rejected) — not free; tune the budget.
- **The point:** 1 thread + a 30 s deadline (0.158) ≈ the 4-REPL pool (0.178) → **the tail, not the core
  count, was the gap.** With the tail removed, the sweep is a clean curve: near-linear to C≈4, plateau 4–6
  (~0.34), **C=7 regresses** on the 8-core box → deployable rule: **MAX_REPLS ≈ cores/2–¾, never = cores.**

Per the [review](REVIEW-v0.1.md): per-core is **~1.7× below paper** on a consistent denominator (~12× at
single-REPL, tail-dominated) — the commodity box is slower per core *and* saturates earlier, which is the
deployability point.

## Layout
| path | what |
|---|---|
| `verify_throughput.py` | the throughput harness v2 (C-sweep → proofs/s, p50, timeout-rate, tail_worktime_share, per-proof rows; `--maxheartbeats`/`--deadline` levers) |
| `SCORECARD-v0.2.md` + `v0.2-*.json`/`.rows.jsonl` | **the current P0 deliverable** (tail-led) + raw per-proof data |
| `SCORECARD-v0.1.md` + `REVIEW-v0.1.md` / `scorecard_v0.1.json` | the superseded v0.1 + its external review (audit trail) |
| `proofs.jsonl` | 300-proof sample of the Goedel-LM/Lean-workbook corpus (public) |
| `deploy/*.yaml` | k3s Deployments for the Kimina Lean Server, Verus, and the verify-gateway |
| `environments/` | the published `verifiers` environments (lean-verifier, spec-faithfulness) |
| `gateway/` | the verify-gateway hardening proxy |

## Run
```bash
# 1. deploy Kimina Lean Server (prebuilt image, Mathlib baked in — no multi-hour build)
kubectl apply -f deploy/kimina-deploy.yaml          # deploys the Kimina server + service
# 2. corpus (or reuse proofs.jsonl)
#    pull from HF datasets-server: Goedel-LM/Lean-workbook-proofs (29.7K verified Lean4 proofs)
# 3. sweep (point --base at the Kimina server). v2 flags:
#    --concurrency 1,2,3,4,5,6,7   client-concurrency levels (server caps at MAX_REPLS)
#    --deadline 190                per-proof client timeout (keep > server MAX_WAIT)
#    --maxheartbeats 200000        rewrite corpus's `maxHeartbeats 0` -> finite (the in-kernel tail lever); omit = as-corpus
#    --tag v0.2-A                  output prefix -> <tag>.json + <tag>.rows.jsonl (per-proof: lat, timeout, lean_err)
python3 verify_throughput.py --base http://<kimina-host>:8000 --n 100 --concurrency 1,2,3,4,5,6,7 --deadline 190 --tag v0.2-A-corpus
# the finite-maxHeartbeats A/B that produced the v0.2 headline:
python3 verify_throughput.py --base http://<kimina-host>:8000 --n 100 --concurrency 1,2,4,6,7 --deadline 190 --maxheartbeats 200000 --tag v0.2-B-finite
```

## Discipline (non-negotiable)
Measure the **substrate**, not math quality (route any proof-quality question to an off-the-shelf prover as a
black box). **Verifier-agnostic** (Lean now; Verus/Rocq later). The point is **on-prem/sovereign packaging +
neutral-measurer credibility**, not a single speed number. **GPU not needed** (verify is CPU-bound).
Measure and harden the substrate under the loop; route proof/spec *quality* to an off-the-shelf prover.

## Status / next
- **P0 done** — scorecard **v0.2** (hung-proof tail measured, two levers A/B'd).
- **P1 done** — tail-rate nailed at n=1000: **4.5 %, 95 % CI [3.2 %, 5.8 %]** (`bootstrap_tail.py`).
- **P2 v0 done** — [`environments/lean_verifier/`](environments/lean_verifier/): an anti-gameable Lean 4
  proving environment for PrimeIntellect `verifiers`, rewarded by a **real Kimina/Lean check**, with the
  scorecard's finite-`maxHeartbeats`/deadline as the safe defaults. Closes the `sorryAx` reward-hack via a
  structured `declaration uses 'sorry'` gate. Validated end-to-end against a live Kimina (`test_local.py`).
- **P2.1 done** — proof-**repair** mode (`repair_turns=k` → `MultiTurnEnv`): feeds Lean's real diagnostics
  back and lets the policy fix, reward = ever-verified, `repair_attempts` monitor (`test_repair.py`).
- **P2 published** — `lean-verifier` is live on the PrimeIntellect Hub:
  [kit-kyo/lean-verifier](https://app.primeintellect.ai/dashboard/environments/kit-kyo/lean-verifier).
- **P2.2 done** — [`environments/spec_faithfulness/`](environments/spec_faithfulness/): a **second verifier
  backend (Verus)** — a spec-writing env where the reward is a **differential Verus check** (a faithful spec
  accepts the correct impl + rejects the buggy ones; the mechanical core of Verus-SpecGym). Verus runs on the
  fleet ([`deploy/verus-deploy.yaml`](deploy/verus-deploy.yaml)); reward validated end-to-end (`test_local.py`:
  faithful 1.0 / too-weak 0.67 / broken 0.0 / cheat 0.0). v0.1 = a 3-problem seed set; Verus-SpecBench next.

- **P2.2 published** — `spec-faithfulness` is live on the Hub:
  [kit-kyo/spec-faithfulness](https://app.primeintellect.ai/dashboard/environments/kit-kyo/spec-faithfulness)
  (16-problem seed set). A unified overview of both envs: [`MODEL-CARD.md`](MODEL-CARD.md).

Next: integrate Verus-SpecBench (581 tasks) + auto-mutants · spec-repair MultiTurnEnv · the P1 verify-gateway
(make the scorecard's deadline + finite-maxHeartbeats levers operational) · RSS-threshold worker recycle (the
Lean leak, lean4 #5321/#6753). Earlier notes:
publish a faithful Lean verification ENVIRONMENT to PrimeIntellect `verifiers` + a spec-faithfulness eval →
ICML 2026 AI-for-Math Workshop.

*Baseline reference: Kimina Lean Server (arXiv 2504.21230), 60-core Xeon 0.83→4.33 proofs/s — this repo is
the commodity/sovereign on-prem end of that curve.*
