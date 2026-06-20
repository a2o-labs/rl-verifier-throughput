# verify-deployability scorecard v0.1 — Lean verification throughput on commodity on-prem CPU

> ⚠️ **Corrected after external review (2026-06-19) — see [`REVIEW-v0.1.md`](REVIEW-v0.1.md); superseded by v0.2.**
> Key corrections to the claims below: (H3) the per-core "same ballpark" used inconsistent denominators —
> on the *same* basis it's **~3.2× below paper (per physical core), ~14× below at single-REPL** (this
> *strengthens* the low-commodity-throughput thesis). (H4) there is **no version-skew evidence** —
> `pass_rate == transport_ok` at every C ⇒ all misses are timeouts, none are Lean errors. (H5) "C=4 > C=7"
> is **1.1% single-run noise**; the real saturation signal is the latency (p50 1.98×). (H1) the corpus is
> **100% `set_option maxHeartbeats 0`** (Lean's compute limit disabled) — this partly produces the hung-proof
> tail, and a finite maxHeartbeats is the cheapest fix (v0.2 A/B). (H6) p99=240 is the client-timeout
> constant, not a percentile. **The real headline — now MEASURED in [`SCORECARD-v0.2.md`](SCORECARD-v0.2.md):
> 4% of proofs eat 52% of single-thread wall; a 30 s per-proof deadline lifts C=1 from 0.069 to 0.158
> proofs/s (2.3×), and a finite maxHeartbeats doubles peak — not more cores.** Single run, no repeats.

*2026-06-19. P0 deliverable of the RL-verifier-throughput topic. Kimina Lean Server 2.0.0 (prebuilt,
Mathlib baked in) on **an 8-core amd64 box (MAX_REPLS=7)**; corpus = first 100 of
`Goedel-LM/Lean-workbook-proofs` (the exact set the Kimina paper benchmarked). Measures verify throughput
vs client-concurrency C — the on-prem-COMMODITY end of the curve (the paper used a 60-core Xeon).*

| C (parallel) | proofs/s | speedup | p50 latency | p99 latency | pass-rate |
|---|---|---|---|---|---|
| 1 | 0.058 | 1.00× | 3.2 s | 240 s* | 0.97 |
| 2 | 0.114 | 1.97× | 3.2 s | 240 s* | 0.97 |
| 4 | 0.178 | 3.07× | 4.1 s | 240 s* | 0.96 |
| 7 | 0.176 | 3.03× | 8.1 s | 240 s* | 0.94 |

cold-start (first verify) **33.9 s** · warm single **27.2 s** · marginal import-Mathlib tax **~6.7 s** (LRU
import-env cache is working: warm < cold). \*p99 = 240 s = the client timeout (see Finding 2).

## Findings (all from this run's data)
1. **An 8-core commodity box saturates at C≈4 REPLs (~0.18 proofs/s); REPLs 5–7 add nothing and hurt
   latency.** Throughput is near-linear 1→2 (1.97×), sub-linear at 4 (3.07×), and FLAT at 7 (3.03×, marginally
   worse) while p50 latency rises 3.2→4.1→8.1 s. **Deployability rule: set MAX_REPLS ≈ cores/2 on a small box;
   over-provisioning to core-count just adds contention.** (Per-core — ⚠️**CORRECTED, see banner/H3**: the
   original "~0.045/core ≈ same ballpark" used inconsistent denominators (ours ÷REPLs vs paper ÷physical-cores).
   On the same basis it is **~3.2× below paper per physical core, ~14× below at single-REPL** — which
   *strengthens* the low-commodity-throughput thesis.)
2. **The tail is dominated by hung / non-terminating proofs.** p50 is healthy (3–8 s) but **p99 is pegged at
   the 240 s client timeout** and mean (17–29 s) >> p50 — a minority of proofs never terminate and burn a full
   timeout each, throttling aggregate throughput. This is the exact failure mode the direction doc flagged,
   now empirically visible → **the #1 P1 target: tuned per-proof timeout + hung-proof isolation/kill** (the
   server's `MAX_WAIT` + a tighter client deadline would lift effective throughput materially).
3. **Cold-start + import-Mathlib tax is real but bounded** (~6.7 s marginal; the LRU env-cache amortizes it).
   The 27 s warm single is proof-specific (proofs[0] is a slow one); the representative warm latency is the
   p50 (3–8 s).
4. ⚠️**RETRACTED (see banner/H4):** the original claim "corpus is mostly v4.26-compatible; the ~3–6% misses
   are version-skew" is **unsupported and self-refuted** — the harness never reads the toolchain version, and
   `pass_rate == transport_ok` at every C ⇒ **zero Lean-error failures ⇒ every miss is a timeout, not a
   version mismatch.** (Throughput is unaffected by pass/fail; both paths consume time.) v2 reports
   `lean_err_rate` separately to keep this honest.

## So what (the thesis, with data)
On commodity/sovereign on-prem hardware, Lean verify-throughput is **low and saturates early** — an 8-core
box tops out near 0.18 proofs/s. For an RL loop that must verify thousands of rollouts per step, this is a
hard, measurable bottleneck (confirms the direction's premise on real hardware). The biggest lever isn't
more cores — it's **killing the hung-proof tail** and **right-sizing the REPL pool** (P1).

## Honest limits
n=100 (vs the paper's 1000) — bounds the run; the scaling SHAPE is clear but absolute numbers would tighten at
n=1000. One backbone (one 8-core box). p99 is censored at the client timeout (240 s) — the true hung-proof
latency is unbounded; we measure the *rate* of hung proofs, not their (infinite) duration. cold-start probe is
proof-specific, so the import-tax estimate is rough.

## NEXT (P1, targets emerged from this data)
- **Hung-proof handling**: tuned timeout + isolation/kill (Finding 2) — measure the throughput delta.
- **REPL right-sizing**: confirm the cores/2 sweet spot across MAX_REPLS sweeps (Finding 1).
- **RSS-threshold worker recycle** (the Lean leak, already have the `MAX_REPL_USES` knob) — measure sustained
  throughput over a long run (does throughput decay without recycling?).
- Scale n→1000 for the publishable baseline; add a second backbone if a clean multi-core node becomes available.

*Backbone: kimina-lean-server:2.0.0. Corpus: Goedel-LM/Lean-workbook-proofs. Raw:
`scorecard_v0.1.json`.*
