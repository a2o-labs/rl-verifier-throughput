# verify-deployability scorecard v0.2 — the hung-proof tail is the bottleneck, not core count

*2026-06-19. Supersedes v0.1 (corrected per [`REVIEW-v0.1.md`](REVIEW-v0.1.md)). Kimina Lean Server 2.0.0
(prebuilt) as a k3s pod on an 8-core amd64 box ( benchmark pod MAX_REPLS=7); corpus = first 100 of
`Goedel-LM/Lean-workbook-proofs` (the corpus the Kimina paper, arXiv 2504.21230 Table 1, reports using).
infotree disabled, client deadline 190 s, per-proof latencies captured. Single run, no repeats.*

## Headline (data-backed): kill the tail, not add cores
On commodity on-prem CPU, Lean verify-throughput is gated by a **small tail of long-running proofs**, and the
cheapest fixes are a per-proof deadline / a finite Lean `maxHeartbeats` — **not more cores.**

- **The tail (as-corpus, `maxHeartbeats 0`, single thread):** 0.069 proofs/s; p50 **2.3 s** but
  **4% of proofs time out (≥190 s) and consume 52% of the serial wall** (`tail_worktime_share`=0.524; at C=1
  work-time = wall, so this is a wall-clock share). (`lean_err 0`, so every miss is a timeout, not a Lean error.)
- **Lever 1 — a per-proof client deadline** (post-hoc, from the captured latencies): a **30 s** deadline takes
  single-thread throughput **0.069 → 0.158 proofs/s (2.3×)**; 20 s → 0.187, 15 s → 0.214, 10 s → 0.260. Pure
  win on wall-clock (no compute freed server-side, but the harness stops waiting).
- **Lever 2 — finite Lean `maxHeartbeats` = 200000** (measured A/B): the in-kernel limit makes runaway proofs
  **fail fast (timeout-rate 4% → 0)**, single-thread **0.069 → 0.109 proofs/s (1.58×)** and **peak throughput
  ~0.18 → ~0.34 (≈1.9×)** across the sweep. **Tradeoff (honest): pass-rate 96% → 93% — ~3 pp of legitimate
  proofs that genuinely need >200 k heartbeats are now rejected** (`lean_err 0 → 7%`, of which 4 pp is the
  old hung tail now failing fast + ~3 pp false-rejects). So `maxHeartbeats` is *not free* — it trades a small
  legit-reject rate for killing the tail; tune the budget to taste. The deadline lever has no false-reject
  but doesn't free server compute as fast.
- **"Single thread + a good timeout ≈ a 4-REPL pool":** 0.158 proofs/s (1 thread, 30 s deadline) ≈ the
  as-corpus 4-REPL pool's 0.178 — i.e. **the tail, not the core count, was most of the gap.**

## Saturation (clean, with the tail removed — finite maxHeartbeats, MAX_REPLS=7 benchmark pod)
| C (parallel) | proofs/s | speedup | p50 | timeout-rate |
|---|---|---|---|---|
| 1 | 0.109 | 1.00× | 2.3 s | 0.00 |
| 2 | 0.202 | 1.87× | 2.3 s | 0.00 |
| 4 | 0.334 | 3.08× | 2.4 s | 0.00 |
| 6 | **0.339** | 3.12× | 3.8 s | 0.00 |
| 7 | 0.259 | 2.39× | 6.8 s | 0.04 |

Clean curve (no tail noise): near-linear to C≈4, plateau at 4–6 (~0.34), **C=7 regresses** (8-core box
over-subscribed: p50 2.4→6.8 s and the timeout tail reappears). → **deployable rule confirmed: MAX_REPLS ≈
cores/2–¾ (4–6 on 8 cores), never = cores.** (v0.1's "C=4 sweet spot" was noisy; this is the de-noised version.)

## vs the paper (consistent denominators)
Kimina paper (arXiv 2504.21230 Table 1): 60-core Xeon, 0.83 → 4.33 proofs/s (8→60 cores). Per physical core,
two of our numbers (reconciled, since both appear in this repo):
- **as-corpus peak** 0.178/8 ≈ 0.022/core → **~3.2× below** paper's 4.33/60 ≈ 0.072 (this is the v0.1
  correction's figure);
- **tail-removed peak** 0.339/8 ≈ 0.043/core → **~1.7× below** — but this compares *our* tail-removed peak to
  the paper's *as-reported* number, so it flatters us if the paper's run also carried an un-removed tail (the
  **paper-side basis is unknown** — they don't report a maxHeartbeats/timeout policy);
- **as-corpus single-REPL** 0.069 vs 0.83 ≈ **12× below**, tail-dominated.

Either way the commodity/sovereign box is slower per core AND saturates much earlier — the deployability point.

## What changed from v0.1 (all per the review)
Per-core compared on a consistent basis (not the flattering "same ballpark"); **no version-skew claim** (all
misses are timeouts, `lean_err`-separated); saturation led by latency + a de-noised finite-maxHeartbeats sweep
(not the 1.1% single-run throughput wiggle); the corpus's `maxHeartbeats 0` is disclosed and turned into the
**A/B that produced the headline**; p99 replaced by timeout-rate + `tail_worktime_share` (REPL-seconds eaten
by timeouts; = wall share only at C=1 — review #1); deadline lever measured.

## P1 — does the 4% tail-rate hold at scale? (n=1000 + bootstrap CI)
The one quantity v0.2 left noisy was the timeout (tail) rate — at n=100, ±1 timeout swings throughput
6–14%. Re-ran **n=1000** as-corpus (`maxHeartbeats 0`, deadline 190 s, C=6 for feasibility; `v0.3-tail1k`),
captured per-proof, then bootstrapped (5000 draws, fixed seed) and split into 10 disjoint 100-proof blocks
(real different samples). Analysis: `bootstrap_tail.py`.

- **The 4% tail-rate holds, and is now tight:** n=1000 timeout-rate **4.50% (45/1000), 95% CI [3.20%, 5.80%]**.
  v0.2's n=100 "4%" was correct — it just wasn't *pinned*.
- **n=100 genuinely IS noisy (the concern was right):** across 10 real disjoint 100-blocks the rate ran
  **[0%, 7%], sd 1.9 pp**, and block throughput swung **2.3×** (0.027–0.063). Bootstrap CI at n=100 = **[1%,
  9%]** vs n=1000 **[3.2%, 5.8%]** → **report the tail-rate with its CI, never as a bare n=100 point value.**
- **The deadline lever survives at scale:** a 30 s post-hoc deadline lifts throughput **2.28×** at n=1000
  (v0.2's C=1 figure was 2.3×) — not a small-sample artifact.
- **Caveat (kept honest):** this run is C=6, so per-proof latency is contention-inflated (good_mean 16.3 s vs
  the C=1 7.2 s) — so the *absolute* throughput and the `tail_worktime_share` (35.5 % here) are **not**
  comparable to v0.2's C=1 numbers; only the C-independent **timeout-rate** (and the deadline *ratio*) carry
  across. The C=1 wall-share headline (52 %) stays a v0.2 n=100 figure; a clean n=1000 C=1 wall-share would
  cost ~4 h and wasn't run.

**Verdict:** v0.2's tail-rate headline is confirmed at publishable scale (4.5 %, CI [3.2 %, 5.8 %]); the lever
ratios are robust; only the small-n point estimate needed the CI the reviewer asked for.

## Honest limits
N=100 (subset of the paper's 1000), single run, no repeats/CI. Cold-start NOT re-measured here (server was
warm; v0.1's 33.9 s/27.2 s was right after a fresh deploy — see L3). The deadline-30 s number is a post-hoc
simulation over the captured latencies, not a live re-run (the latencies are real; the cut is arithmetic).
The `maxHeartbeats` false-reject rate (~3 pp) is corpus-specific. Lean/Mathlib versions are pinned only by the
movable image tag `:2.0.0` (record the in-image `lean-toolchain`/Mathlib commit before publishing — L7).

## Next
~~Confirm with repeats/CI (the tail rate is the noisy quantity)~~ → **DONE (P1 section above): 4.5 %, CI
[3.2 %, 5.8 %] at n=1000.** Remaining: record the pinned Lean/Mathlib; the P1 hardening to implement +
measure: **RSS-threshold worker recycle** (does sustained throughput decay without it?) and a **per-proof
deadline + heartbeat-budget knob** wired into the server (the two levers above, made operational). Then P2: a
faithful PrimeIntellect `verifiers` env + spec-faithfulness eval.

*Raw: `v0.2-A-corpus.json`/`.rows.jsonl` (tail) + `v0.2-B-finite.json`/`.rows.jsonl` (A/B + saturation) +
`v0.3-tail1k.json`/`.rows.jsonl` (n=1000 tail-rate CI, via `bootstrap_tail.py`).*
